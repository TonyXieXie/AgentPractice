import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { AgentStep } from '../api';
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

function AgentStepView({ steps, streaming }: AgentStepViewProps) {
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
                return (
                    <div key={`${step.step_type}-${index}`} className={`agent-step ${category}`}>
                        <div className="agent-step-header">
                            <span className="agent-step-category">{CATEGORY_LABELS[category]}</span>
                            <span className="agent-step-type">{step.step_type}</span>
                        </div>
                        <div className="agent-step-content">{renderRichContent(step.content || '')}</div>
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
