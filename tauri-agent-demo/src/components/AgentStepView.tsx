import { useState, Fragment } from 'react';
import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { AgentStep } from '../api';
import { ToolPermissionRequest } from '../types';
import DiffView from './DiffView';
import './AgentStepView.css';

type Category = 'thought' | 'tool' | 'final' | 'error' | 'other';

interface AgentStepViewProps {
    steps: AgentStep[];
    streaming?: boolean;
    pendingPermission?: ToolPermissionRequest | null;
    onPermissionDecision?: (status: 'approved' | 'denied') => void;
    permissionBusy?: boolean;
}

const CATEGORY_LABELS: Record<Category, string> = {
    thought: 'Thought Process',
    tool: 'Tool Call',
    final: 'Final Answer',
    error: 'Error',
    other: 'Output'
};

const STEP_CATEGORY: Record<AgentStep['step_type'], Category> = {
    thought: 'thought',
    thought_delta: 'thought',
    action: 'tool',
    action_delta: 'tool',
    observation: 'tool',
    answer: 'final',
    answer_delta: 'final',
    error: 'error'
};

const markdown = new MarkdownIt({
    html: false,
    linkify: true,
    breaks: true
});

markdown.use(texmath, {
    engine: katex,
    delimiters: ['brackets', 'dollars', 'beg_end'],
    katexOptions: { throwOnError: false, errorColor: '#f87171' }
});

function renderRichContent(content: string) {
    const normalized = (content || '')
        .replace(/\r\n/g, '\n')
        .replace(/\\\\\[/g, '\\[')
        .replace(/\\\\\]/g, '\\]')
        .replace(/\\\\\(/g, '\\(')
        .replace(/\\\\\)/g, '\\)')
        .replace(/^\s*\\\[\s*$/gm, '\n\\[\n')
        .replace(/^\s*\\\]\s*$/gm, '\n\\]\n');
    const html = markdown.render(normalized);
    return <div className="content-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

function looksLikeDiff(content: string) {
    return /^diff --git /m.test(content) || /^@@\s+-\d+/m.test(content);
}

function hasUnicodeEscapes(content: string) {
    return /\\u[0-9a-fA-F]{4}/.test(content) || /\\\\u[0-9a-fA-F]{4}/.test(content);
}

function decodeUnicodeEscapes(content: string) {
    if (!content) return content;
    return content
        .replace(/\\\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
        .replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

function AgentStepView({
    steps,
    streaming,
    pendingPermission,
    onPermissionDecision,
    permissionBusy
}: AgentStepViewProps) {
    const [expandedObservations, setExpandedObservations] = useState<Record<string, boolean>>({});
    const [translatedSearchSteps, setTranslatedSearchSteps] = useState<Record<string, boolean>>({});

    if (!steps.length && !pendingPermission) {
        return (
            <div className="agent-steps empty">
                <span>No agent output yet.</span>
            </div>
        );
    }

    const permissionAnchorIndex = pendingPermission
        ? (() => {
            for (let i = steps.length - 1; i >= 0; i -= 1) {
                const step = steps[i];
                if (step.step_type !== 'action') continue;
                const toolName = String(step.metadata?.tool || '');
                if (toolName.toLowerCase() === 'run_shell' || String(step.content || '').startsWith('run_shell[')) {
                    return i;
                }
            }
            for (let i = steps.length - 1; i >= 0; i -= 1) {
                const step = steps[i];
                const category = STEP_CATEGORY[step.step_type] || 'other';
                if (category === 'tool') {
                    return i;
                }
            }
            return -1;
        })()
        : -1;

    const permissionCard = pendingPermission ? (
        <div className="agent-step tool permission">
            <div className="agent-step-header">
                <span className="agent-step-category">Tool Call</span>
                <span className="agent-step-type">permission</span>
            </div>
            <div className="agent-step-content">
                <div className="permission-card">
                    <div className="permission-title">Shell 权限请求</div>
                    <div className="permission-subtitle">该命令不在 allowlist 中，是否允许执行？</div>
                    <div className="permission-command">{pendingPermission.path}</div>
                    {pendingPermission.reason && (
                        <div className="permission-reason">{pendingPermission.reason}</div>
                    )}
                    <div className="permission-actions">
                        <button
                            type="button"
                            className="permission-btn deny"
                            onClick={() => onPermissionDecision && onPermissionDecision('denied')}
                            disabled={permissionBusy || !onPermissionDecision}
                        >
                            拒绝
                        </button>
                        <button
                            type="button"
                            className="permission-btn allow"
                            onClick={() => onPermissionDecision && onPermissionDecision('approved')}
                            disabled={permissionBusy || !onPermissionDecision}
                        >
                            允许
                        </button>
                    </div>
                </div>
            </div>
        </div>
    ) : null;

    return (
        <div className="agent-steps">
            {steps.map((step, index) => {
                const category = STEP_CATEGORY[step.step_type] || 'other';
                const stepKey = `${step.step_type}-${index}`;
                const isObservation = step.step_type === 'observation';
                const isAction = step.step_type === 'action' || step.step_type === 'action_delta';
                const rawContent = String(step.content || '');
                const trimmedContent = rawContent.trimStart();
                const lowerContent = trimmedContent.toLowerCase();
                const toolName = String(step.metadata?.tool || '').toLowerCase();
                const looksLikeSearchCall =
                    /\bsearch\s*\[/i.test(rawContent) || /\bsearch\s*\(/i.test(rawContent) || lowerContent.startsWith('search');
                const canTranslateSearch = isAction && (toolName === 'search' || looksLikeSearchCall) && hasUnicodeEscapes(rawContent);
                const isTranslated = !!translatedSearchSteps[stepKey];
                const displayContent = canTranslateSearch && isTranslated ? decodeUnicodeEscapes(rawContent) : rawContent;
                let observationText = '';
                let observationPreview = '';
                let observationHasMore = false;
                let observationIsDiff = false;
                if (isObservation) {
                    observationText = rawContent.replace(/\r\n/g, '\n');
                    const lines = observationText.split('\n');
                    observationHasMore = lines.length > 1;
                    observationPreview = observationHasMore ? `${lines[0]} ...` : lines[0] || '';
                    observationIsDiff = looksLikeDiff(observationText);
                }

                return (
                    <Fragment key={`${step.step_type}-${index}`}>
                        <div className={`agent-step ${category}${isAction ? ' action' : ''}`}>
                            <div className="agent-step-header">
                                <span className="agent-step-category">{CATEGORY_LABELS[category]}</span>
                                <span className="agent-step-type">{step.step_type}</span>
                            </div>
                            {isObservation ? (
                                <div className="agent-step-content observation">
                                    {expandedObservations[stepKey] ? (
                                        observationIsDiff ? (
                                            <DiffView content={observationText} />
                                        ) : (
                                            <pre className="observation-text expanded">{observationText}</pre>
                                        )
                                    ) : (
                                        <pre className="observation-text">{observationPreview}</pre>
                                    )}
                                    {observationHasMore && (
                                        <button
                                            type="button"
                                            className="observation-toggle"
                                            aria-label={expandedObservations[stepKey] ? 'Collapse observation' : 'Expand observation'}
                                            title={expandedObservations[stepKey] ? 'Collapse' : 'Expand'}
                                            onClick={() =>
                                                setExpandedObservations((prev) => ({ ...prev, [stepKey]: !prev[stepKey] }))
                                            }
                                        >
                                            <span aria-hidden="true">
                                                {expandedObservations[stepKey] ? '^' : 'v'}
                                            </span>
                                        </button>
                                    )}
                                </div>
                            ) : (
                                <div className={`agent-step-content${isAction ? ' action-content' : ''}`}>
                                    {renderRichContent(displayContent)}
                                    {canTranslateSearch && (
                                        <button
                                            type="button"
                                            className="action-translate-btn"
                                            onClick={() =>
                                                setTranslatedSearchSteps((prev) => ({ ...prev, [stepKey]: !prev[stepKey] }))
                                            }
                                        >
                                            {isTranslated ? '原文' : '翻译'}
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                        {pendingPermission && index === permissionAnchorIndex && permissionCard}
                    </Fragment>
                );
            })}
            {pendingPermission && permissionAnchorIndex < 0 && permissionCard}
            {streaming && (
                <div className="agent-step status">
                    <div className="agent-step-header">
                        <span className="agent-step-category">Streaming</span>
                        <span className="agent-step-type">in-progress</span>
                    </div>
                    <div className="agent-step-content">
                        <span className="content-text">Waiting for next step...</span>
                    </div>
                </div>
            )}
        </div>
    );
}

export default AgentStepView;
