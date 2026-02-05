import { useState, useEffect, useRef, Fragment, type MouseEventHandler } from 'react';
import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { openPath, openUrl, revealItemInDir } from '@tauri-apps/plugin-opener';
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
    onRetryMessage?: () => void;
    onRollbackMessage?: () => void;
    onRevertPatch?: (patch: string) => void;
    patchRevertBusy?: boolean;
    onOpenWorkFile?: (filePath: string) => void;
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

markdown.renderer.rules.fence = (tokens, idx, _options, _env, _self) => {
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

type ApplyPatchResult = {
    ok: boolean;
    summary?: { path: string; added: number; removed: number }[];
    diff?: string;
    revert_patch?: string;
    error?: string;
};

function parseApplyPatchResult(content: string): ApplyPatchResult | null {
    if (!content) return null;
    try {
        const parsed = JSON.parse(content);
        if (parsed && typeof parsed === 'object' && typeof parsed.ok === 'boolean') {
            return parsed as ApplyPatchResult;
        }
    } catch {
        // ignore
    }
    return null;
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

type LinkToken =
    | { type: 'text'; value: string }
    | { type: 'url'; value: string }
    | { type: 'file'; value: string };

const LINK_PATTERN = /((?:https?|file):\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+|[a-zA-Z]:\\[^\s<>"'`]+|\\\\[^\s<>"'`]+|\/[^\s<>"'`]+)/g;
const TRAILING_PUNCTUATION = /[),.;:!?]+$/;

function splitTrailingPunctuation(value: string) {
    const match = value.match(TRAILING_PUNCTUATION);
    if (!match) {
        return { value, trailing: '' };
    }
    return {
        value: value.slice(0, -match[0].length),
        trailing: match[0]
    };
}

function tokenizeLinks(text: string): LinkToken[] {
    if (!text) return [{ type: 'text', value: '' }];
    const tokens: LinkToken[] = [];
    let lastIndex = 0;
    LINK_PATTERN.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = LINK_PATTERN.exec(text))) {
        const start = match.index;
        const raw = match[0];
        if (start > lastIndex) {
            tokens.push({ type: 'text', value: text.slice(lastIndex, start) });
        }
        const { value, trailing } = splitTrailingPunctuation(raw);
        if (value) {
            const isUrl = /^(?:https?:\/\/|file:\/\/|www\.)/i.test(value);
            tokens.push({ type: isUrl ? 'url' : 'file', value });
        }
        if (trailing) {
            tokens.push({ type: 'text', value: trailing });
        }
        lastIndex = start + raw.length;
    }
    if (lastIndex < text.length) {
        tokens.push({ type: 'text', value: text.slice(lastIndex) });
    }
    return tokens;
}

function getParentPath(filePath: string) {
    const trimmed = filePath.replace(/[\\/]+$/, '');
    const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
    if (idx < 0) {
        return trimmed;
    }
    const parent = trimmed.slice(0, idx);
    if (/^[a-zA-Z]:$/.test(parent)) {
        return `${parent}\\`;
    }
    return parent || trimmed;
}

const hasScheme = (value: string) => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);

const isFileHref = (value: string) => {
    if (!value) return false;
    if (value.startsWith('#')) return false;
    if (/^file:\/\//i.test(value)) return true;
    if (/^[a-zA-Z]:[\\/]/.test(value)) return true;
    if (value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) return true;
    return !hasScheme(value);
};

const normalizeFileHref = (value: string) => {
    if (!value) return '';
    if (value.startsWith('file://')) {
        try {
            const url = new URL(value);
            let pathname = decodeURIComponent(url.pathname || '');
            if (/^\/[A-Za-z]:\//.test(pathname)) {
                pathname = pathname.slice(1);
            }
            return pathname;
        } catch {
            return value;
        }
    }
    return value;
};

function AgentStepView({
    steps,
    streaming,
    pendingPermission,
    onPermissionDecision,
    permissionBusy,
    onRetryMessage,
    onRollbackMessage,
    onRevertPatch,
    patchRevertBusy,
    onOpenWorkFile
}: AgentStepViewProps) {
    const [expandedObservations, setExpandedObservations] = useState<Record<string, boolean>>({});
    const [translatedSearchSteps, setTranslatedSearchSteps] = useState<Record<string, boolean>>({});
    const [expandedErrors, setExpandedErrors] = useState<Record<string, boolean>>({});
    const [errorCopyState, setErrorCopyState] = useState<Record<string, 'idle' | 'copied' | 'failed'>>({});
    const [thoughtAutoScroll, setThoughtAutoScroll] = useState<Record<string, boolean>>({});
    const [expandedPatches, setExpandedPatches] = useState<Record<string, boolean>>({});
    const thoughtRefs = useRef<Record<string, HTMLDivElement | null>>({});
    const [fileMenu, setFileMenu] = useState<{ path: string; x: number; y: number } | null>(null);

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

    useEffect(() => {
        if (!fileMenu) return;
        const dismiss = () => setFileMenu(null);
        window.addEventListener('click', dismiss);
        window.addEventListener('blur', dismiss);
        return () => {
            window.removeEventListener('click', dismiss);
            window.removeEventListener('blur', dismiss);
        };
    }, [fileMenu]);

    const handleOpenLink = async (href: string) => {
        if (!href) return;
        const normalized = href.startsWith('www.') ? `https://${href}` : href;
        try {
            await openUrl(normalized);
        } catch {
            // ignore open errors
        }
    };

    const handleOpenFile = async (filePath: string) => {
        if (!filePath) return;
        if (onOpenWorkFile) {
            try {
                await onOpenWorkFile(filePath);
                return;
            } catch (error) {
                console.error('Failed to open work window for file:', error);
            }
        }
        try {
            await openPath(filePath);
        } catch {
            // ignore open errors
        }
    };

    const handleOpenFileFolder = async (filePath: string) => {
        if (!filePath) return;
        try {
            await revealItemInDir(filePath);
        } catch {
            try {
                const parent = getParentPath(filePath);
                await openPath(parent || filePath);
            } catch {
                // ignore open errors
            }
        }
    };

    const renderLinkedText = (text: string) => {
        const tokens = tokenizeLinks(text);
        return tokens.map((token, idx) => {
            if (token.type === 'text') {
                return token.value;
            }
            if (token.type === 'url') {
                const href = token.value.startsWith('www.') ? `https://${token.value}` : token.value;
                return (
                    <a
                        key={`${token.type}-${idx}`}
                        href={href}
                        className="content-link"
                        onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            handleOpenLink(href);
                        }}
                    >
                        {token.value}
                    </a>
                );
            }
            return (
                <button
                    key={`${token.type}-${idx}`}
                    type="button"
                    className="file-link"
                    onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        handleOpenFile(token.value);
                    }}
                    onContextMenu={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        setFileMenu({ path: token.value, x: event.clientX, y: event.clientY });
                    }}
                >
                    {token.value}
                </button>
            );
        });
    };

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

    const handleContentClick: MouseEventHandler<HTMLDivElement> = (event) => {
        const target = event.target as HTMLElement;
        const link = target.closest<HTMLAnchorElement>('a');
        if (!link || !link.href) return;
        const rawHref = link.getAttribute('href') || '';
        const href = rawHref || link.href;
        if (isFileHref(rawHref || href)) {
            event.preventDefault();
            event.stopPropagation();
            const filePath = normalizeFileHref(rawHref || href);
            if (onOpenWorkFile) {
                void onOpenWorkFile(filePath);
                return;
            }
            void handleOpenFile(filePath);
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        handleOpenLink(href);
    };

    const handleCopyError = (stepKey: string, text: string) => {
        if (!text) return;
        navigator.clipboard.writeText(text).then(
            () => {
                setErrorCopyState((prev) => ({ ...prev, [stepKey]: 'copied' }));
                window.setTimeout(() => {
                    setErrorCopyState((prev) => ({ ...prev, [stepKey]: 'idle' }));
                }, 1200);
            },
            () => {
                setErrorCopyState((prev) => ({ ...prev, [stepKey]: 'failed' }));
                window.setTimeout(() => {
                    setErrorCopyState((prev) => ({ ...prev, [stepKey]: 'idle' }));
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

    const lastPatchIndex = (() => {
        let idx = -1;
        steps.forEach((step, index) => {
            if (step.step_type !== 'observation') return;
            const toolName = String(step.metadata?.tool || '').toLowerCase();
            if (toolName === 'apply_patch') {
                idx = index;
            }
        });
        return idx;
    })();

    return (
        <>
            <div className="agent-steps">
            {steps.map((step, index) => {
                const category = STEP_CATEGORY[step.step_type] || 'other';
                const stepKey = `${step.step_type}-${index}`;
                const isObservation = step.step_type === 'observation';
                const isAction = step.step_type === 'action' || step.step_type === 'action_delta';
                const isError = step.step_type === 'error';
                const errorType = String(step.metadata?.error_type || '').toLowerCase();
                const isTimeoutError =
                    isError && (errorType.includes('timeout') || /timeout/i.test(String(step.content || '')));
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
                let applyPatchResult: ApplyPatchResult | null = null;
                let isApplyPatch = false;
                if (isObservation) {
                    observationText = rawContent.replace(/\r\n/g, '\n');
                    const lines = observationText.split('\n');
                    observationHasMore = lines.length > 1;
                    observationPreview = observationHasMore ? `${lines[0]} ...` : lines[0] || '';
                    observationIsDiff = looksLikeDiff(observationText);
                    isApplyPatch = toolName === 'apply_patch';
                    if (isApplyPatch) {
                        applyPatchResult = parseApplyPatchResult(observationText);
                    }
                }
                const showObservationToggle = isObservation && observationHasMore && !isApplyPatch;

                return (
                    <Fragment key={`${step.step_type}-${index}`}>
                        <div className={`agent-step ${category}${isAction ? ' action' : ''}`}>
                            <div className="agent-step-header">
                                    <span className="agent-step-category">{CATEGORY_LABELS[category]}</span>
                                    <div className="agent-step-header-actions">
                                        <span className="agent-step-type">{step.step_type}</span>
                                        {showObservationToggle && (
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
                            </div>
                            {isObservation ? (
                                <div className="agent-step-content observation">
                                    {isApplyPatch && applyPatchResult ? (
                                        applyPatchResult.ok ? (
                                            <div className="patch-result">
                                                <div className="patch-summary-header">
                                                    <span className="patch-summary-title">修改</span>
                                                    <div className="patch-summary-actions">
                                                        <button
                                                            type="button"
                                                            className="patch-summary-btn"
                                                            onClick={() =>
                                                                setExpandedPatches((prev) => ({
                                                                    ...prev,
                                                                    [stepKey]: !prev[stepKey]
                                                                }))
                                                            }
                                                        >
                                                            {expandedPatches[stepKey] ? '收起' : '展开'}
                                                        </button>
                                                        {index === lastPatchIndex && onRevertPatch && applyPatchResult.revert_patch && (
                                                            <button
                                                                type="button"
                                                                className="patch-summary-btn danger"
                                                                onClick={() => onRevertPatch(applyPatchResult!.revert_patch || '')}
                                                                disabled={patchRevertBusy}
                                                            >
                                                                {patchRevertBusy ? '处理中...' : '撤销'}
                                                            </button>
                                                        )}
                                                    </div>
                                                </div>
                                                <div className="patch-summary-list">
                                                    {(applyPatchResult.summary || []).length === 0 && (
                                                        <div className="patch-summary-item">
                                                            <span className="patch-summary-path">无变更</span>
                                                            <span className="patch-summary-counts">+0 -0</span>
                                                        </div>
                                                    )}
                                                    {(applyPatchResult.summary || []).map((item, idx) => (
                                                        <div key={`${item.path}-${idx}`} className="patch-summary-item">
                                                            <span className="patch-summary-path">{item.path}</span>
                                                            <span className="patch-summary-counts">
                                                                +{item.added} -{item.removed}
                                                            </span>
                                                        </div>
                                                    ))}
                                                </div>
                                                {expandedPatches[stepKey] && applyPatchResult.diff && (
                                                    <DiffView content={applyPatchResult.diff} />
                                                )}
                                            </div>
                                        ) : (
                                            <div className="observation-text">{applyPatchResult.error || 'Apply patch failed.'}</div>
                                        )
                                    ) : expandedObservations[stepKey] ? (
                                        observationIsDiff ? (
                                            <DiffView content={observationText} />
                                        ) : (
                                            <div className="observation-text expanded">{renderLinkedText(observationText)}</div>
                                        )
                                    ) : (
                                        <div className="observation-text">{renderLinkedText(observationPreview)}</div>
                                    )}
                                </div>
                            ) : (
                                <div
                                    className={`agent-step-content${isAction ? ' action-content' : ''}`}
                                    ref={isThought ? (el) => { thoughtRefs.current[stepKey] = el; } : undefined}
                                    onScroll={isThought ? handleThoughtScroll(stepKey) : undefined}
                                >
                                    {renderRichContent(displayContent, (event) => {
                                        handleMarkdownClick(event);
                                        handleContentClick(event);
                                    })}
                                    {hasErrorDetails && (
                                        <>
                                            <div className="error-actions">
                                                <button
                                                    type="button"
                                                    className="error-toggle"
                                                    onClick={() =>
                                                        setExpandedErrors((prev) => ({ ...prev, [stepKey]: !prev[stepKey] }))
                                                    }
                                                >
                                                    {errorExpanded ? '隐藏错误详情' : '显示错误详情'}
                                                </button>
                                                <button
                                                    type="button"
                                                    className={`error-copy-btn ${errorCopyState[stepKey] || 'idle'}`}
                                                    onClick={() => handleCopyError(stepKey, errorDetails)}
                                                >
                                                    {errorCopyState[stepKey] === 'copied'
                                                        ? '已复制'
                                                        : errorCopyState[stepKey] === 'failed'
                                                            ? '复制失败'
                                                            : '复制错误'}
                                                </button>
                                            </div>
                                            {errorExpanded && (
                                                <pre className="error-details">{errorDetails}</pre>
                                            )}
                                        </>
                                    )}
                                    {isTimeoutError && (onRetryMessage || onRollbackMessage) && (
                                        <div className="timeout-banner">
                                            <span>请求超时</span>
                                            <div className="timeout-actions">
                                                {onRollbackMessage && (
                                                    <button type="button" onClick={onRollbackMessage}>
                                                        撤销本次
                                                    </button>
                                                )}
                                                {onRetryMessage && (
                                                    <button type="button" onClick={onRetryMessage}>
                                                        重新发送
                                                    </button>
                                                )}
                                            </div>
                                        </div>
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
            {fileMenu && (
            <div
                className="file-context-menu"
                style={{
                    top: Math.min(fileMenu.y, window.innerHeight - 90),
                    left: Math.min(fileMenu.x, window.innerWidth - 180)
                }}
                onClick={(event) => event.stopPropagation()}
                onContextMenu={(event) => event.preventDefault()}
            >
                <button
                    type="button"
                    className="file-context-item"
                    onClick={() => {
                        handleOpenFile(fileMenu.path);
                        setFileMenu(null);
                    }}
                >
                    打开文件
                </button>
                <button
                    type="button"
                    className="file-context-item"
                    onClick={() => {
                        handleOpenFileFolder(fileMenu.path);
                        setFileMenu(null);
                    }}
                >
                    打开所在文件夹
                </button>
            </div>
            )}
        </>
    );
}

export default AgentStepView;
