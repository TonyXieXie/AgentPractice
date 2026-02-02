import { useState } from 'react';
import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { AgentStep } from '../api';
import DiffView from './DiffView';
import './AgentStepView.css';

type Category = 'thought' | 'tool' | 'final' | 'error' | 'other';

interface AgentStepViewProps {
    steps: AgentStep[];
    streaming?: boolean;
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

function AgentStepView({ steps, streaming }: AgentStepViewProps) {
    const [expandedObservations, setExpandedObservations] = useState<Record<string, boolean>>({});

    if (!steps.length) {
        return (
            <div className="agent-steps empty">
                <span>No agent output yet.</span>
            </div>
        );
    }

    return (
        <div className="agent-steps">
            {steps.map((step, index) => {
                const category = STEP_CATEGORY[step.step_type] || 'other';
                const stepKey = `${step.step_type}-${index}`;
                const isObservation = step.step_type === 'observation';
                let observationText = '';
                let observationPreview = '';
                let observationHasMore = false;
                let observationIsDiff = false;
                if (isObservation) {
                    observationText = String(step.content || '').replace(/\r\n/g, '\n');
                    const lines = observationText.split('\n');
                    observationHasMore = lines.length > 1;
                    observationPreview = observationHasMore ? `${lines[0]} ...` : lines[0] || '';
                    observationIsDiff = looksLikeDiff(observationText);
                }

                return (
                    <div key={`${step.step_type}-${index}`} className={`agent-step ${category}`}>
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
                            <div className="agent-step-content">{renderRichContent(step.content || '')}</div>
                        )}
                    </div>
                );
            })}
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
