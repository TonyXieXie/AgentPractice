import { useState, useEffect, useRef, Fragment, type MouseEventHandler } from 'react';
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

markdown.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const token = tokens[idx];
    const info = (token.info || '').trim();
    const lang = info ? info.split(/\s+/g)[0] : '';
    const safeLang = lang.replace(/[^a-zA-Z0-9_-]/g, '');
    const label = safeLang || 'text';
    const code = markdown.utils.escapeHtml(token.content || '');
    const langClass = safeLang ? `language-${safeLang}` : '';
    return [
        '<div class="code-block-container">',
        '<div class="code-block-header">',
        `<span class="code-block-lang">${label}</span>`,
        '<button class="copy-code-btn" type="button">Copy</button>',
        '</div>',
        `<pre><code class="${langClass}">${code}</code></pre>`,
        '</div>'
    ].join('');
};

function renderRichContent(content: string, onClick?: MouseEventHandler<HTMLDivElement>) {
    const normalized = (content || '')
        .replace(/\r\n/g, '\n')
        .replace(/\\\\\[/g, '\\[')
        .replace(/\\\\\]/g, '\\]')
        .replace(/\\\\\(/g, '\\(')
        .replace(/\\\\\)/g, '\\)')
        .replace(/^\s*\\\[\s*$/gm, '\n\\[\n')
        .replace(/^\s*\\\]\s*$/gm, '\n\\]\n');
    const html = markdown.render(normalized);
    return <div className="content-markdown" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
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
    const [expandedErrors, setExpandedErrors] = useState<Record<string, boolean>>({});
    const [thoughtAutoScroll, setThoughtAutoScroll] = useState<Record<string, boolean>>({});
    const thoughtRefs = useRef<Record<string, HTMLDivElement | null>>({});

    useEffect(() => {
        steps.forEach((step, index) => {
            const category = STEP_CATEGORY[step.step_type] || 'other';
            if (category !== 'thought') return;
            const stepKey = `${step.step_type}-${index}`;
            const el = thoughtRefs.current[stepKey];
            if (!el) return;
            const auto = thoughtAutoScroll[stepKey];
            if (auto === false) return;
            el.scrollTop = el.scrollHeight;
        });
    }, [steps, thoughtAutoScroll]);

    const handleThoughtScroll = (stepKey: string) => (event: React.UIEvent<HTMLDivElement>) => {
        const el = event.currentTarget;
        const threshold = 8;
        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
        setThoughtAutoScroll((prev) => {
            if (prev[stepKey] === nearBottom) return prev;
            return { ...prev, [stepKey]: nearBottom };
        });
    };

    const handleMarkdownClick: MouseEventHandler<HTMLDivElement> = (event) => {
        const target = event.target as HTMLElement;
        const button = target.closest<HTMLButtonElement>('.copy-code-btn');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        const container = button.closest<HTMLElement>('.code-block-container');
        const codeEl = container?.querySelector('pre code');
        const text = codeEl?.textContent ?? '';
        if (!text) return;

        navigator.clipboard.writeText(text).then(
            () => {
                button.textContent = 'Copied';
                button.classList.add('copied');
                window.setTimeout(() => {
                    button.textContent = 'Copy';
                    button.classList.remove('copied');
                }, 1200);
            },
            () => {
                button.textContent = 'Failed';
                window.setTimeout(() => {
                    button.textContent = 'Copy';
                }, 1200);
            }
        );
    };

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
                const isError = step.step_type === 'error';
                const isThought = category === 'thought';
                const rawContent = String(step.content || '');
                const trimmedContent = rawContent.trimStart();
                const lowerContent = trimmedContent.toLowerCase();
                const toolName = String(step.metadata?.tool || '').toLowerCase();
                const looksLikeSearchCall =
                    /\bsearch\s*\[/i.test(rawContent) || /\bsearch\s*\(/i.test(rawContent) || lowerContent.startsWith('search');
                const canTranslateSearch = isAction && (toolName === 'search' || looksLikeSearchCall) && hasUnicodeEscapes(rawContent);
                const isTranslated = !!translatedSearchSteps[stepKey];
                const displayContent = canTranslateSearch && isTranslated ? decodeUnicodeEscapes(rawContent) : rawContent;
                const errorDetails = isError ? String(step.metadata?.traceback || '') : '';
                const hasErrorDetails = Boolean(errorDetails);
                const errorExpanded = !!expandedErrors[stepKey];
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
                                <div
                                    className={`agent-step-content${isAction ? ' action-content' : ''}`}
                                    ref={isThought ? (el) => { thoughtRefs.current[stepKey] = el; } : undefined}
                                    onScroll={isThought ? handleThoughtScroll(stepKey) : undefined}
                                >
                                    {renderRichContent(displayContent, handleMarkdownClick)}
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
                                    {hasErrorDetails && (
                                        <>
                                            <button
                                                type="button"
                                                className="error-toggle"
                                                onClick={() =>
                                                    setExpandedErrors((prev) => ({ ...prev, [stepKey]: !prev[stepKey] }))
                                                }
                                            >
                                                {errorExpanded ? '隐藏错误详情' : '显示错误详情'}
                                            </button>
                                            {errorExpanded && (
                                                <pre className="error-details">{errorDetails}</pre>
                                            )}
                                        </>
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
