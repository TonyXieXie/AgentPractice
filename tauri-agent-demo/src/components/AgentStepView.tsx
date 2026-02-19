import {
    useState,
    useEffect,
    useLayoutEffect,
    useRef,
    useMemo,
    Fragment,
    type MouseEventHandler,
    type ReactElement
} from 'react';
import mermaid from 'mermaid';
import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { openPath, openUrl, revealItemInDir } from '@tauri-apps/plugin-opener';
import { API_BASE_URL, AgentStep } from '../api';
import { ToolPermissionRequest, AstPayload } from '../types';
import DiffView from './DiffView';
import AstViewer from './AstViewer';
import './AgentStepView.css';

type Category = 'thought' | 'tool' | 'final' | 'error' | 'other';

interface AgentStepViewProps {
    steps: AgentStep[];
    messageId?: number;
    streaming?: boolean;
    pendingPermission?: ToolPermissionRequest | null;
    onPermissionDecision?: (status: 'approved' | 'approved_once' | 'denied') => void;
    permissionBusy?: boolean;
    onRetryMessage?: () => void;
    onRollbackMessage?: () => void;
    onRevertPatch?: (patch: string, messageId?: number) => void;
    patchRevertBusy?: boolean;
    onOpenWorkFile?: (filePath: string, line?: number, column?: number) => void;
    currentWorkPath?: string;
    debugActive?: boolean;
    onOpenDebugCall?: (iteration: number) => void;
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
    error: 'error',
    context_estimate: 'other'
};

const IS_MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.userAgent);
const MAC_ABSOLUTE_PREFIX = /^(Users|Volumes|private|System|Library|Applications|opt|etc|var|tmp)[\\/]/;
const FILE_EXISTS_TIMEOUT_MS = 1500;

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
    const raw = token.content || '';
    const code = markdown.utils.escapeHtml(raw);
    const langClass = safeLang ? `language-${safeLang}` : '';
    if (safeLang.toLowerCase() === 'mermaid') {
        const encoded = encodeURIComponent(raw);
        return [
            '<div class="mermaid-block">',
            '<div class="mermaid-controls">',
            '<button type="button" class="mermaid-control" data-action="zoom-in" aria-label="Zoom in">+</button>',
            '<button type="button" class="mermaid-control" data-action="zoom-out" aria-label="Zoom out">-</button>',
            '<button type="button" class="mermaid-control" data-action="reset" aria-label="Reset zoom">Reset</button>',
            '</div>',
            '<div class="mermaid-viewport">',
            `<div class="mermaid" data-raw="${encoded}">${code}</div>`,
            '</div>',
            '</div>'
        ].join('');
    }
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

markdown.renderer.rules.code_inline = (tokens, idx, _options, _env, _self) => {
    const token = tokens[idx];
    const content = token.content || '';
    const escaped = markdown.utils.escapeHtml(content);
    const parsed = parseFileReference(content.trim());
    if (parsed?.path) {
        return `<code class="file-code-link">${escaped}</code>`;
    }
    return `<code>${escaped}</code>`;
};

const MERMAID_START_RE =
    /^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|journey|pie|gitGraph|mindmap|timeline|quadrantChart|sankey-beta)\b/i;
const MERMAID_HINT_RE =
    /(-->|==>|subgraph|participant|class|state|section|task|journey|pie|gantt|mindmap|timeline|quadrantChart|sankey|flowchart|graph)\b/i;

const injectMermaidFences = (content: string) => {
    if (!content) return content;
    if (/```mermaid/i.test(content)) return content;
    const normalized = content.replace(/\r\n/g, '\n');
    const parts = normalized.split(/\n{2,}/);
    let changed = false;
    const next = parts.map((part) => {
        const trimmed = part.trim();
        if (!trimmed) return part;
        if (MERMAID_START_RE.test(trimmed) && MERMAID_HINT_RE.test(trimmed)) {
            changed = true;
            return `\`\`\`mermaid\n${trimmed}\n\`\`\``;
        }
        return part;
    });
    return changed ? next.join('\n\n') : normalized;
};

function renderMarkdownHtml(content: string) {
    const normalized = (content || '')
        .replace(/\r\n/g, '\n')
        .replace(/\\\\\[/g, '\\[')
        .replace(/\\\\\]/g, '\\]')
        .replace(/\\\\\(/g, '\\(')
        .replace(/\\\\\)/g, '\\)')
        .replace(/^\s*\\\[\s*$/gm, '\n\\[\n')
        .replace(/^\s*\\\]\s*$/gm, '\n\\]\n');
    return markdown.render(injectMermaidFences(normalized));
}

function RichContent({ content, onClick }: { content: string; onClick?: MouseEventHandler<HTMLDivElement> }) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const html = useMemo(() => renderMarkdownHtml(content), [content]);

    useLayoutEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        if (el.dataset.html === html) return;
        el.innerHTML = html;
        el.dataset.html = html;
    }, [html]);

    return <div className="content-markdown" onClick={onClick} ref={containerRef} />;
}

function looksLikeDiff(content: string) {
    return /^diff --git /m.test(content) || /^@@\s+-\d+/m.test(content);
}

function parseExitCode(content: string) {
    if (!content) return null;
    const match = content.match(/\[exit_code\s*=\s*(-?\d+)\]/i);
    if (!match) return null;
    const value = Number(match[1]);
    return Number.isFinite(value) ? value : null;
}

type ShellHeader = {
    pty_id?: string;
    status?: string;
    pty?: boolean;
    exit_code?: number;
    idle_timeout?: number;
    buffer_size?: number;
    cursor?: number;
    reset?: boolean;
    pty_fallback?: boolean;
};

function parseShellHeaderLine(line: string) {
    const match = line.match(/^\[([^\]]+)\](.*)$/);
    if (!match) return { header: null as ShellHeader | null, extraText: '' };
    const header: ShellHeader = {};
    const parseToken = (token: string) => {
        if (!token.includes('=')) return false;
        const [rawKey, rawValue] = token.split('=');
        if (!rawKey || rawValue === undefined) return false;
        const key = rawKey.trim().toLowerCase();
        const value = rawValue.trim();
        const lower = value.toLowerCase();
        switch (key) {
            case 'pty_id':
                header.pty_id = value;
                return true;
            case 'status':
                header.status = value;
                return true;
            case 'pty':
                header.pty = lower === 'true';
                return true;
            case 'exit_code': {
                const num = Number(value);
                if (Number.isFinite(num)) header.exit_code = num;
                return true;
            }
            case 'idle_timeout': {
                const num = Number(value);
                if (Number.isFinite(num)) header.idle_timeout = num;
                return true;
            }
            case 'buffer_size': {
                const num = Number(value);
                if (Number.isFinite(num)) header.buffer_size = num;
                return true;
            }
            case 'cursor': {
                const num = Number(value);
                if (Number.isFinite(num)) header.cursor = num;
                return true;
            }
            case 'reset':
                header.reset = lower === 'true';
                return true;
            case 'pty_fallback':
                header.pty_fallback = lower === 'true';
                return true;
            default:
                return false;
        }
    };

    match[1].trim().split(/\s+/).forEach(parseToken);
    const extraTokens = (match[2] || '').trim().split(/\s+/).filter(Boolean);
    const extras: string[] = [];
    extraTokens.forEach((token) => {
        if (!parseToken(token)) {
            extras.push(token);
        }
    });
    return { header, extraText: extras.join(' ') };
}

function parseShellOutput(raw: string) {
    if (!raw) return null;
    const normalized = raw.replace(/\r\n/g, '\n');
    const lines = normalized.split('\n');
    if (!lines.length) return null;
    const { header, extraText } = parseShellHeaderLine(lines[0].trim());
    if (!header) return null;
    const rest = lines.slice(1);
    if (extraText) {
        rest.unshift(extraText);
    }
    return {
        header,
        body: rest.join('\n')
    };
}

type ApplyPatchResult = {
    ok: boolean;
    summary?: { path: string; added: number; removed: number }[];
    diff?: string;
    revert_patch?: string;
    error?: string;
};

const parseAstPayload = (raw: string): AstPayload | null => {
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
            return parsed as AstPayload;
        }
    } catch {
        const start = raw.indexOf('{');
        const end = raw.lastIndexOf('}');
        if (start >= 0 && end > start) {
            try {
                const parsed = JSON.parse(raw.slice(start, end + 1));
                if (parsed && typeof parsed === 'object') {
                    return parsed as AstPayload;
                }
            } catch {
                return null;
            }
        }
    }
    return null;
};

type DiffChunk = { path: string; diff: string };

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

function extractJsonFinalAnswer(content: string) {
    const trimmed = content.trim();
    if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return null;
    try {
        const parsed = JSON.parse(trimmed) as Record<string, unknown> | null;
        if (!parsed || typeof parsed !== 'object') return null;
        const keys = ['final_answer', 'answer', 'final', 'output', 'response', 'content'];
        for (const key of keys) {
            const value = parsed[key];
            if (typeof value === 'string' && value.trim()) return value;
        }
    } catch {
        // ignore parse errors
    }
    return null;
}

function splitDiffByFile(diff: string, fallbackPath?: string): DiffChunk[] {
    if (!diff) return [];
    const lines = diff.replace(/\r\n/g, '\n').split('\n');
    const chunks: DiffChunk[] = [];
    let currentPath = '';
    let buffer: string[] = [];
    const flush = () => {
        if (!buffer.length) return;
        const path = currentPath || fallbackPath || 'patch';
        chunks.push({ path, diff: buffer.join('\n') });
        buffer = [];
    };
    for (const line of lines) {
        const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
        if (match) {
            flush();
            currentPath = match[2] || match[1] || currentPath;
        }
        buffer.push(line);
    }
    flush();
    if (!chunks.length && diff.trim()) {
        chunks.push({ path: fallbackPath || 'patch', diff });
    }
    return chunks;
}

type StepItem = {
    iteration: number | null;
    element: ReactElement;
};

type GroupOptions = {
    debugActive?: boolean;
    onOpenDebugCall?: (iteration: number) => void;
};

const getIterationValue = (step: AgentStep): number | null => {
    const raw = (step.metadata as any)?.iteration;
    if (typeof raw === 'number') {
        return Number.isFinite(raw) ? raw : null;
    }
    if (typeof raw === 'string') {
        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
};

const groupStepElements = (items: StepItem[], options?: GroupOptions) => {
    const groups: ReactElement[] = [];
    let currentIteration: number | null = null;
    let currentElements: ReactElement[] = [];
    const showDebug = Boolean(options?.debugActive && options?.onOpenDebugCall);

    const flush = () => {
        if (currentIteration === null || currentElements.length === 0) {
            currentElements = [];
            currentIteration = null;
            return;
        }
        const iterationValue = currentIteration;
        const iterationLabel = `Round ${iterationValue + 1}`;
        groups.push(
            <div key={`group-${iterationValue}-${groups.length}`} className="agent-step-group">
                <div className="agent-step-group-header">
                    <span className="agent-step-group-label">{iterationLabel}</span>
                    {showDebug && (
                        <button
                            type="button"
                            className="agent-step-group-debug"
                            onClick={() => options?.onOpenDebugCall?.(iterationValue)}
                            aria-label={`Open debug for ${iterationLabel}`}
                            title="Open debug"
                        >
                            Debug
                        </button>
                    )}
                </div>
                <div className="agent-step-group-body">{currentElements}</div>
            </div>
        );
        currentElements = [];
        currentIteration = null;
    };

    items.forEach((item, idx) => {
        if (item.iteration === null) {
            flush();
            groups.push(
                <div key={`group-solo-${idx}`} className="agent-step-group solo">
                    {item.element}
                </div>
            );
            return;
        }

        if (currentIteration === null || currentIteration !== item.iteration) {
            flush();
            currentIteration = item.iteration;
        }
        currentElements.push(item.element);
    });

    flush();
    return groups;
};

type LinkToken =
    | { type: 'text'; value: string }
    | { type: 'url'; value: string }
    | { type: 'file'; value: string };
type FileCheckStatus = 'exists' | 'missing' | 'error';
type FileCheckEntry = { status: FileCheckStatus; checkedAt: number };
type FileLinkStatus = FileCheckStatus | 'pending' | 'unknown';

const LINK_PATTERN =
    /((?:https?|file):\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+|[a-zA-Z]:\\[^\s<>"'`]+|\\\\[^\s<>"'`]+|\/[^\s<>"'`]+|[^\s<>"'`]+[\\/][^\s<>"'`]+\.[A-Za-z0-9]{1,10}(?::\d+(?::\d+)?|#L\d+(?:C\d+)?)?|[^\s<>"'`]+\.[A-Za-z0-9]{1,10}(?::\d+(?::\d+)?|#L\d+(?:C\d+)?)?)/g;
const TRAILING_PUNCTUATION = /[),.;:!?]+$/;
const FILE_EXT_PATTERN = /\.[A-Za-z0-9]{1,10}$/;
const ESCAPE_SEGMENTS = new Set(['n', 'r', 't', 'b', 'f', 'v', '0']);
const FILE_HASH_PATTERN = /^(.*)#L(\d+)(?:C(\d+))?$/i;
const FILE_LINE_PATTERN = /^(.*?)(?::(\d+))(?:[:#](\d+))?$/;

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

function looksLikeFilePath(value: string) {
    if (!value) return false;
    if (/^www\./i.test(value)) return false;
    if (/^file:\/\//i.test(value)) return true;
    if (/^[a-zA-Z]:[\\/]/.test(value)) {
        const rest = value.slice(3);
        const firstSegment = rest.split(/[\\/]/)[0];
        if (firstSegment.length === 1 && ESCAPE_SEGMENTS.has(firstSegment.toLowerCase())) {
            return false;
        }
        return true;
    }
    if (value.startsWith('\\\\')) return true;
    if (value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) return true;
    if (value.includes('/') || value.includes('\\')) return FILE_EXT_PATTERN.test(value);
    return FILE_EXT_PATTERN.test(value);
}

function parseFileReference(rawValue: string) {
    const trimmed = rawValue.trim();
    if (!trimmed) return null;
    const { value } = splitTrailingPunctuation(trimmed);
    if (!value) return null;
    const hashMatch = value.match(FILE_HASH_PATTERN);
    if (hashMatch) {
        const path = hashMatch[1];
        if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
            return {
                path,
                line: Number(hashMatch[2]),
                column: hashMatch[3] ? Number(hashMatch[3]) : undefined
            };
        }
    }
    const lineMatch = value.match(FILE_LINE_PATTERN);
    if (lineMatch) {
        const path = lineMatch[1];
        if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
            return {
                path,
                line: Number(lineMatch[2]),
                column: lineMatch[3] ? Number(lineMatch[3]) : undefined
            };
        }
    }
    if (looksLikeFilePath(value)) {
        return { path: value };
    }
    return null;
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
            if (isUrl) {
                tokens.push({ type: 'url', value });
            } else {
                const parsed = parseFileReference(value);
                if (parsed?.path) {
                    tokens.push({ type: 'file', value });
                } else {
                    tokens.push({ type: 'text', value });
                }
            }
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

function joinPath(base: string, child: string) {
    if (!base) return child;
    const separator = base.includes('\\') ? '\\' : '/';
    if (base.endsWith('\\') || base.endsWith('/')) {
        return `${base}${child}`;
    }
    return `${base}${separator}${child}`;
}

const hasScheme = (value: string) => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);
const isAbsolutePath = (value: string) => /^(?:[a-zA-Z]:[\\/]|\\\\|\/)/.test(value);

const isFileHref = (value: string) => {
    if (!value) return false;
    if (value.startsWith('#')) return false;
    const parsed = parseFileReference(value);
    if (!parsed?.path) return false;
    const candidate = parsed.path;
    if (/^file:\/\//i.test(candidate)) return true;
    if (/^[a-zA-Z]:[\\/]/.test(candidate)) return true;
    if (candidate.startsWith('/') || candidate.startsWith('./') || candidate.startsWith('../')) return true;
    return !hasScheme(candidate);
};

const normalizeFileHref = (value: string) => {
    if (!value) return '';
    if (IS_MAC && !value.startsWith('/') && !value.startsWith('./') && !value.startsWith('../')) {
        if (!/^[a-zA-Z]:[\\/]/.test(value) && !/^file:\/\//i.test(value) && MAC_ABSOLUTE_PREFIX.test(value)) {
            value = `/${value}`;
        }
    }
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
    messageId,
    streaming,
    pendingPermission,
    onPermissionDecision,
    permissionBusy,
    onRetryMessage,
    onRollbackMessage,
    onRevertPatch,
    patchRevertBusy,
    onOpenWorkFile,
    currentWorkPath,
    debugActive,
    onOpenDebugCall
}: AgentStepViewProps) {
    const [expandedObservations, setExpandedObservations] = useState<Record<string, boolean>>({});
    const [translatedSearchSteps, setTranslatedSearchSteps] = useState<Record<string, boolean>>({});
    const [expandedErrors, setExpandedErrors] = useState<Record<string, boolean>>({});
    const [errorCopyState, setErrorCopyState] = useState<Record<string, 'idle' | 'copied' | 'failed'>>({});
    const [thoughtAutoScroll, setThoughtAutoScroll] = useState<Record<string, boolean>>({});
    const [expandedPatches, setExpandedPatches] = useState<Record<string, boolean>>({});
    const [expandedAstRaw, setExpandedAstRaw] = useState<Record<string, boolean>>({});
    const thoughtRefs = useRef<Record<string, HTMLDivElement | null>>({});
    const [fileMenu, setFileMenu] = useState<{ path: string; x: number; y: number } | null>(null);
    const [patchSummaryExpanded, setPatchSummaryExpanded] = useState(false);
    const mermaidRootRef = useRef<HTMLDivElement | null>(null);
    const mermaidInitializedRef = useRef(false);
    const [fileValidationTick, setFileValidationTick] = useState(0);
    const fileExistsCacheRef = useRef<Map<string, FileCheckEntry>>(new Map());
    const pendingFileChecksRef = useRef<Set<string>>(new Set());
    const MERMAID_MIN_SCALE = 0.2;
    const MERMAID_MAX_SCALE = 4;
    const MERMAID_ZOOM_STEP = 0.15;

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



    const ensureMermaid = () => {
        if (mermaidInitializedRef.current) return;
        mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            theme: 'dark'
        });
        mermaidInitializedRef.current = true;
    };

    const setupMermaidInteractions = () => {
        const root = mermaidRootRef.current;
        if (!root) return;
        const blocks = root.querySelectorAll<HTMLElement>('.mermaid-block');
        blocks.forEach((block) => {
            const viewport = block.querySelector<HTMLElement>('.mermaid-viewport');
            if (!viewport) return;

            const readState = () => {
                const scale = Number.parseFloat(block.dataset.scale || '1');
                const x = Number.parseFloat(block.dataset.x || '0');
                const y = Number.parseFloat(block.dataset.y || '0');
                return {
                    scale: Number.isFinite(scale) ? scale : 1,
                    x: Number.isFinite(x) ? x : 0,
                    y: Number.isFinite(y) ? y : 0
                };
            };

            const applyTransform = () => {
                const svg = block.querySelector<SVGElement>('.mermaid svg');
                if (!svg) return;
                const { scale, x, y } = readState();
                svg.style.transformOrigin = '0 0';
                svg.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
                svg.style.cursor = block.dataset.dragging === 'true' ? 'grabbing' : 'grab';
            };

            if (!block.dataset.scale) {
                block.dataset.scale = '1';
                block.dataset.x = '0';
                block.dataset.y = '0';
            }

            applyTransform();

            if (block.dataset.mermaidInteractive === 'true') return;
            block.dataset.mermaidInteractive = 'true';

            const clamp = (value: number) =>
                Math.min(MERMAID_MAX_SCALE, Math.max(MERMAID_MIN_SCALE, value));

            const setState = (next: { scale: number; x: number; y: number }) => {
                block.dataset.scale = String(next.scale);
                block.dataset.x = String(next.x);
                block.dataset.y = String(next.y);
                applyTransform();
            };

            const handleWheel = (event: WheelEvent) => {
                if (!event.ctrlKey && !event.metaKey) return;
                event.preventDefault();
                const state = readState();
                const direction = event.deltaY > 0 ? -1 : 1;
                const factor = direction > 0 ? 1 + MERMAID_ZOOM_STEP : 1 - MERMAID_ZOOM_STEP;
                const nextScale = clamp(state.scale * factor);
                setState({ ...state, scale: nextScale });
            };

            const handlePointerDown = (event: PointerEvent) => {
                if (event.button !== 0) return;
                event.preventDefault();
                viewport.setPointerCapture(event.pointerId);
                const state = readState();
                block.dataset.dragging = 'true';
                block.dataset.startX = String(event.clientX);
                block.dataset.startY = String(event.clientY);
                block.dataset.originX = String(state.x);
                block.dataset.originY = String(state.y);
                applyTransform();
            };

            const handlePointerMove = (event: PointerEvent) => {
                if (block.dataset.dragging !== 'true') return;
                const startX = Number.parseFloat(block.dataset.startX || '0');
                const startY = Number.parseFloat(block.dataset.startY || '0');
                const originX = Number.parseFloat(block.dataset.originX || '0');
                const originY = Number.parseFloat(block.dataset.originY || '0');
                const dx = event.clientX - startX;
                const dy = event.clientY - startY;
                const state = readState();
                setState({ ...state, x: originX + dx, y: originY + dy });
            };

            const handlePointerUp = (event: PointerEvent) => {
                if (block.dataset.dragging !== 'true') return;
                block.dataset.dragging = 'false';
                viewport.releasePointerCapture(event.pointerId);
                applyTransform();
            };

            const handleDoubleClick = () => {
                setState({ scale: 1, x: 0, y: 0 });
            };

            const handleControlClick = (event: MouseEvent) => {
                const target = event.target as HTMLElement;
                const actionEl = target.closest<HTMLElement>('.mermaid-control');
                if (!actionEl) return;
                const action = actionEl.dataset.action || '';
                const state = readState();
                if (action === 'zoom-in') {
                    setState({ ...state, scale: clamp(state.scale + MERMAID_ZOOM_STEP) });
                } else if (action === 'zoom-out') {
                    setState({ ...state, scale: clamp(state.scale - MERMAID_ZOOM_STEP) });
                } else if (action === 'reset') {
                    setState({ scale: 1, x: 0, y: 0 });
                }
                event.preventDefault();
                event.stopPropagation();
            };

            viewport.addEventListener('wheel', handleWheel, { passive: false });
            viewport.addEventListener('pointerdown', handlePointerDown);
            viewport.addEventListener('pointermove', handlePointerMove);
            viewport.addEventListener('pointerup', handlePointerUp);
            viewport.addEventListener('pointercancel', handlePointerUp);
            viewport.addEventListener('dblclick', handleDoubleClick);
            block.addEventListener('click', handleControlClick);
        });
    };

    const runMermaid = () => {
        const root = mermaidRootRef.current;
        if (!root) return;
        const nodes = Array.from(root.querySelectorAll<HTMLElement>('.mermaid'));
        if (!nodes.length) return;
        ensureMermaid();
        const pendingNodes = nodes.filter((node) => !node.querySelector('svg'));
        if (!pendingNodes.length) {
            setupMermaidInteractions();
            return;
        }
        const logMermaid = (level: 'debug' | 'warn' | 'error', message: string, detail?: unknown) => {
            if (level === 'debug' && !debugActive) return;
            const payload = detail === undefined ? ['[mermaid]', message] : ['[mermaid]', message, detail];
            if (level === 'debug') {
                console.debug(...payload);
            } else if (level === 'warn') {
                console.warn(...payload);
            } else {
                console.error(...payload);
            }
        };
        logMermaid('debug', 'runMermaid', { total: nodes.length, pending: pendingNodes.length });
        const mermaidParser = (
            mermaid as unknown as {
                parse?: (text: string, parseOptions?: { suppressErrors?: boolean }) => Promise<boolean | void>;
            }
        ).parse;
        const normalizeMermaidSource = (value: string) => {
            if (!value) return value;
            let changed = false;
            const detectMermaidDiagramType = (input: string) => {
                const lines = input.split('\n');
                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;
                    if (trimmed.startsWith('%%')) continue;
                    const match = trimmed.match(
                        /^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|journey|pie|gitGraph|mindmap|timeline|quadrantChart|sankey-beta)\b/i
                    );
                    if (match) return match[1].toLowerCase();
                    break;
                }
                return '';
            };
            const arrowTokens = ['-->', '==>', '-.->', '<-->', '<--', '<=='];
            const matchArrow = (text: string, index: number) => {
                for (const arrow of arrowTokens) {
                    if (text.startsWith(arrow, index)) return arrow;
                }
                return null;
            };
            const splitConcatenatedEdges = (line: string) => {
                if (!/(-->|==>|-\.\->|<--|<-->)/.test(line)) return line;
                let out = '';
                let i = 0;
                let depthSquare = 0;
                let depthRound = 0;
                let depthCurly = 0;
                let inSingle = false;
                let inDouble = false;
                let foundEdge = false;
                const isEscaped = (text: string, index: number) => {
                    let backslashes = 0;
                    for (let j = index - 1; j >= 0 && text[j] === '\\'; j -= 1) backslashes += 1;
                    return backslashes % 2 === 1;
                };
                while (i < line.length) {
                    const ch = line[i];
                    if (!inDouble && ch === "'" && !isEscaped(line, i)) {
                        inSingle = !inSingle;
                        out += ch;
                        i += 1;
                        continue;
                    }
                    if (!inSingle && ch === '"' && !isEscaped(line, i)) {
                        inDouble = !inDouble;
                        out += ch;
                        i += 1;
                        continue;
                    }
                    if (!inSingle && !inDouble) {
                        if (ch === '[') depthSquare += 1;
                        else if (ch === ']') depthSquare = Math.max(0, depthSquare - 1);
                        else if (ch === '(') depthRound += 1;
                        else if (ch === ')') depthRound = Math.max(0, depthRound - 1);
                        else if (ch === '{') depthCurly += 1;
                        else if (ch === '}') depthCurly = Math.max(0, depthCurly - 1);
                    }
                    if (
                        !inSingle &&
                        !inDouble &&
                        depthSquare === 0 &&
                        depthRound === 0 &&
                        depthCurly === 0
                    ) {
                        const prev = i > 0 ? line[i - 1] : '';
                        const canStart = i === 0 || /\s/.test(prev) || /[\]\)\}]/.test(prev);
                        if (canStart && /[A-Za-z0-9_]/.test(ch)) {
                            let j = i;
                            while (j < line.length && /[A-Za-z0-9_]/.test(line[j])) j += 1;
                            let k = j;
                            while (k < line.length && /\s/.test(line[k])) k += 1;
                            const arrow = matchArrow(line, k);
                            if (arrow) {
                                if (foundEdge) {
                                    const trimmed = out.replace(/[ \t]+$/, '');
                                    out = trimmed.endsWith('\n') ? trimmed : `${trimmed}\n`;
                                    changed = true;
                                }
                                foundEdge = true;
                            }
                        }
                    }
                    out += ch;
                    i += 1;
                }
                return out;
            };
            const escapeLabelBrackets = (label: string) => {
                const replace = (text: string) => text.replace(/\[/g, '&#91;').replace(/\]/g, '&#93;');
                if (label.startsWith('[') && label.endsWith(']') && label.length > 1) {
                    const inner = label.slice(1, -1);
                    const escapedInner = replace(inner);
                    const next = `[${escapedInner}]`;
                    if (next !== label) changed = true;
                    return next;
                }
                const escapedLabel = replace(label);
                if (escapedLabel !== label) changed = true;
                return escapedLabel;
            };
            const normalizeLabel = (label: string) => {
                const bracketEscaped = escapeLabelBrackets(label);
                if (!/[()]/.test(bracketEscaped)) return bracketEscaped;
                const trimmed = bracketEscaped.trim();
                const isQuoted =
                    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
                    (trimmed.startsWith("'") && trimmed.endsWith("'"));
                if (!isQuoted) {
                    let quote = '"';
                    if (bracketEscaped.includes('"') && !bracketEscaped.includes("'")) {
                        quote = "'";
                    } else if (bracketEscaped.includes('"') && bracketEscaped.includes("'")) {
                        const encoded = bracketEscaped.replace(/\(/g, '&#40;').replace(/\)/g, '&#41;');
                        if (encoded !== bracketEscaped) changed = true;
                        return encoded;
                    }
                    changed = true;
                    return `${quote}${bracketEscaped}${quote}`;
                }
                return bracketEscaped;
            };
            const normalizeSquareLabels = (input: string) => {
                let out = '';
                let inSquare = false;
                let nestedSquare = 0;
                let escaped = false;
                let buffer = '';
                for (let i = 0; i < input.length; i += 1) {
                    const ch = input[i];
                    if (escaped) {
                        if (inSquare) {
                            buffer += ch;
                        } else {
                            out += ch;
                        }
                        escaped = false;
                        continue;
                    }
                    if (ch === '\\') {
                        if (inSquare) {
                            buffer += ch;
                        } else {
                            out += ch;
                        }
                        escaped = true;
                        continue;
                    }
                    if (ch === '[' && !inSquare) {
                        inSquare = true;
                        nestedSquare = 0;
                        buffer = '';
                        out += ch;
                        continue;
                    }
                    if (ch === '[' && inSquare) {
                        nestedSquare += 1;
                        buffer += ch;
                        continue;
                    }
                    if (ch === ']' && inSquare) {
                        if (nestedSquare > 0) {
                            nestedSquare -= 1;
                            buffer += ch;
                            continue;
                        }
                        inSquare = false;
                        out += normalizeLabel(buffer);
                        out += ch;
                        continue;
                    }
                    if (inSquare) {
                        buffer += ch;
                    } else {
                        out += ch;
                    }
                }
                if (inSquare && buffer) {
                    out += buffer;
                }
                return out;
            };
            const normalizeCurlyLabels = (input: string) => {
                let out = '';
                let inCurly = false;
                let nestedCurly = 0;
                let escaped = false;
                let buffer = '';
                for (let i = 0; i < input.length; i += 1) {
                    const ch = input[i];
                    if (escaped) {
                        if (inCurly) {
                            buffer += ch;
                        } else {
                            out += ch;
                        }
                        escaped = false;
                        continue;
                    }
                    if (ch === '\\') {
                        if (inCurly) {
                            buffer += ch;
                        } else {
                            out += ch;
                        }
                        escaped = true;
                        continue;
                    }
                    if (ch === '{' && !inCurly) {
                        inCurly = true;
                        nestedCurly = 0;
                        buffer = '';
                        out += ch;
                        continue;
                    }
                    if (ch === '{' && inCurly) {
                        nestedCurly += 1;
                        buffer += ch;
                        continue;
                    }
                    if (ch === '}' && inCurly) {
                        if (nestedCurly > 0) {
                            nestedCurly -= 1;
                            buffer += ch;
                            continue;
                        }
                        inCurly = false;
                        out += normalizeLabel(buffer);
                        out += ch;
                        continue;
                    }
                    if (inCurly) {
                        buffer += ch;
                    } else {
                        out += ch;
                    }
                }
                if (inCurly && buffer) {
                    out += buffer;
                }
                return out;
            };
            const stripTrailingEscapedNewlines = (line: string) => {
                const isOutsideIndex = (text: string, index: number) => {
                    let depthSquare = 0;
                    let depthRound = 0;
                    let depthCurly = 0;
                    let inSingle = false;
                    let inDouble = false;
                    let escapedChar = false;
                    for (let i = 0; i < text.length; i += 1) {
                        if (i === index) {
                            return (
                                !inSingle &&
                                !inDouble &&
                                depthSquare === 0 &&
                                depthRound === 0 &&
                                depthCurly === 0
                            );
                        }
                        const ch = text[i];
                        if (escapedChar) {
                            escapedChar = false;
                            continue;
                        }
                        if (ch === '\\') {
                            escapedChar = true;
                            continue;
                        }
                        if (!inDouble && ch === "'") {
                            inSingle = !inSingle;
                            continue;
                        }
                        if (!inSingle && ch === '"') {
                            inDouble = !inDouble;
                            continue;
                        }
                        if (!inSingle && !inDouble) {
                            if (ch === '[') depthSquare += 1;
                            else if (ch === ']') depthSquare = Math.max(0, depthSquare - 1);
                            else if (ch === '(') depthRound += 1;
                            else if (ch === ')') depthRound = Math.max(0, depthRound - 1);
                            else if (ch === '{') depthCurly += 1;
                            else if (ch === '}') depthCurly = Math.max(0, depthCurly - 1);
                        }
                    }
                    return false;
                };
                let next = line;
                let updated = false;
                while (true) {
                    const trimmed = next.replace(/[ \t]+$/, '');
                    if (!trimmed.endsWith('n')) break;
                    let backslashCount = 0;
                    let cursor = trimmed.length - 2;
                    while (cursor >= 0 && trimmed[cursor] === '\\') {
                        backslashCount += 1;
                        cursor -= 1;
                    }
                    if (backslashCount === 0) break;
                    const start = trimmed.length - 1 - backslashCount;
                    if (!isOutsideIndex(trimmed, start)) break;
                    next = trimmed.slice(0, start);
                    updated = true;
                }
                if (updated) changed = true;
                return next;
            };
            const normalized = value
                .replace(/\r\n/g, '\n')
                .replace(/\r/g, '\n')
                .replace(/[\u2028\u2029]/g, '\n')
                .split('\n')
                .map((line) => stripTrailingEscapedNewlines(splitConcatenatedEdges(line)))
                .join('\n');
            const diagramType = detectMermaidDiagramType(normalized);
            const shouldNormalizeLabels = !['classdiagram', 'erdiagram'].includes(diagramType);
            const escaped = shouldNormalizeLabels
                ? normalizeCurlyLabels(normalizeSquareLabels(normalized))
                : normalized;
            if (changed) logMermaid('debug', 'normalized mermaid source');
            return escaped;
        };
        pendingNodes.forEach((node, idx) => {
            node.removeAttribute('data-processed');
            const raw = node.dataset.raw;
            let source = '';
            if (raw) {
                try {
                    source = decodeURIComponent(raw);
                } catch {
                    source = raw;
                }
                node.textContent = source;
            } else {
                source = node.textContent ?? '';
            }
            const normalized = normalizeMermaidSource(source);
            if (normalized !== source) {
                source = normalized;
                node.textContent = source;
                node.dataset.raw = encodeURIComponent(source);
                node.dataset.mermaidNormalized = 'true';
            }
            if (!source.trim()) {
                logMermaid('warn', 'empty mermaid source', { index: idx });
            } else if (typeof mermaidParser === 'function') {
                try {
                    void mermaidParser(source, { suppressErrors: false }).catch((error) => {
                        logMermaid('warn', 'parse failed', {
                            index: idx,
                            error,
                            preview: source.slice(0, 240)
                        });
                        node.dataset.mermaidParseError = 'true';
                    });
                } catch (error) {
                    logMermaid('warn', 'parse threw', {
                        index: idx,
                        error,
                        preview: source.slice(0, 240)
                    });
                    node.dataset.mermaidParseError = 'true';
                }
            }
        });
        mermaid.run({ nodes: pendingNodes }).then(
            () => {
                setupMermaidInteractions();
                pendingNodes.forEach((node, idx) => {
                    if (node.querySelector('svg')) return;
                    const raw = node.dataset.raw;
                    if (!raw) {
                        logMermaid('warn', 'missing data-raw for fallback render', { index: idx });
                        return;
                    }
                    let source = raw;
                    try {
                        source = decodeURIComponent(raw);
                    } catch {
                        // ignore decode errors
                    }
                    const id = `mermaid-${Date.now()}-${idx}`;
                    mermaid.render(id, source).then(
                        ({ svg }) => {
                            node.innerHTML = svg;
                            setupMermaidInteractions();
                        },
                        (error) => {
                            logMermaid('warn', 'render failed', {
                                index: idx,
                                error,
                                preview: source.slice(0, 240)
                            });
                        }
                    );
                });
            },
            (error) => {
                logMermaid('warn', 'run failed', error);
            }
        );
    };

    const markFileCodeLinks = () => {
        const root = mermaidRootRef.current;
        if (!root) return;
        const nodes = root.querySelectorAll<HTMLElement>('.content-markdown code');
        nodes.forEach((node) => {
            if (node.closest('pre')) {
                node.classList.remove('file-code-link');
                return;
            }
            const text = node.textContent?.trim() ?? '';
            const parsed = parseFileReference(text);
            if (parsed?.path) {
                node.classList.add('file-code-link');
            } else {
                node.classList.remove('file-code-link');
            }
        });
    };

    const resolvePathForCheck = (rawPath: string) => {
        const parsed = parseFileReference(rawPath);
        const normalized = normalizeFileHref(parsed?.path ?? rawPath);
        if (!normalized) return { path: '', checkable: false };
        if (isAbsolutePath(normalized)) return { path: normalized, checkable: true };
        if (!currentWorkPath) return { path: normalized, checkable: false };
        if (normalized.startsWith('./') || normalized.startsWith('../') || normalized.includes('/') || normalized.includes('\\')) {
            return { path: joinPath(currentWorkPath, normalized), checkable: true };
        }
        return { path: normalized, checkable: false };
    };

    const checkFileExists = async (resolvedPath: string) => {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), FILE_EXISTS_TIMEOUT_MS);
        try {
            const response = await fetch(
                `${API_BASE_URL}/local-file-exists?path=${encodeURIComponent(resolvedPath)}`,
                { signal: controller.signal }
            );
            if (!response.ok) {
                throw new Error(`Backend check failed (${response.status})`);
            }
            const data = await response.json();
            const exists = Boolean(data?.exists);
            fileExistsCacheRef.current.set(resolvedPath, { status: exists ? 'exists' : 'missing', checkedAt: Date.now() });
        } catch {
            fileExistsCacheRef.current.set(resolvedPath, { status: 'error', checkedAt: Date.now() });
        } finally {
            pendingFileChecksRef.current.delete(resolvedPath);
            window.clearTimeout(timeout);
            setFileValidationTick((prev) => prev + 1);
        }
    };

    const ensureFileCheck = (rawPath: string) => {
        const { path, checkable } = resolvePathForCheck(rawPath);
        if (!checkable || !path) return;
        const cached = fileExistsCacheRef.current.get(path);
        if (cached && cached.status !== 'error') return;
        if (cached && cached.status === 'error' && Date.now() - cached.checkedAt < 10_000) return;
        if (pendingFileChecksRef.current.has(path)) return;
        pendingFileChecksRef.current.add(path);
        void checkFileExists(path);
    };

    const getFileLinkStatus = (rawPath: string) => {
        const { path, checkable } = resolvePathForCheck(rawPath);
        if (!checkable || !path) return { status: 'unknown', checkable: false };
        const cached = fileExistsCacheRef.current.get(path);
        if (!cached) {
            ensureFileCheck(rawPath);
            return { status: 'pending', checkable: true };
        }
        if (cached.status === 'error' && Date.now() - cached.checkedAt >= 10_000) {
            fileExistsCacheRef.current.delete(path);
            ensureFileCheck(rawPath);
            return { status: 'pending', checkable: true };
        }
        return { status: cached.status, checkable: true };
    };

    const markFileTextLinks = () => {
        const root = mermaidRootRef.current;
        if (!root) return;
        const containers = root.querySelectorAll<HTMLElement>('.content-markdown');
        containers.forEach((container) => {
            const walker = document.createTreeWalker(
                container,
                NodeFilter.SHOW_TEXT,
                {
                    acceptNode: (node) => {
                        const text = node.nodeValue || '';
                        if (!text.trim()) return NodeFilter.FILTER_REJECT;
                        const parent = (node as Text).parentElement;
                        if (!parent) return NodeFilter.FILTER_REJECT;
                        if (parent.closest('a, button, code, pre, .code-block-container, .mermaid')) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        return NodeFilter.FILTER_ACCEPT;
                    }
                } as unknown as NodeFilter
            );
            const textNodes: Text[] = [];
            while (walker.nextNode()) {
                textNodes.push(walker.currentNode as Text);
            }
            textNodes.forEach((node) => {
                const text = node.nodeValue || '';
                const tokens = tokenizeLinks(text);
                if (tokens.length === 1 && tokens[0].type === 'text') return;
                const fragment = document.createDocumentFragment();
                tokens.forEach((token) => {
                    if (token.type === 'file') {
                        const linkStatus = getFileLinkStatus(token.value);
                        const shouldLink = !linkStatus.checkable || linkStatus.status === 'exists';
                        if (!shouldLink) {
                            fragment.appendChild(document.createTextNode(token.value));
                            return;
                        }
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'file-link file-link-inline';
                        button.textContent = token.value;
                        button.addEventListener('click', (event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            handleOpenFile(token.value);
                        });
                        fragment.appendChild(button);
                    } else {
                        fragment.appendChild(document.createTextNode(token.value));
                    }
                });
                node.parentNode?.replaceChild(fragment, node);
            });
        });
    };

    useEffect(() => {
        if (!steps.length && !pendingPermission) return;
        const frame = window.requestAnimationFrame(() => {
            runMermaid();
            markFileCodeLinks();
            markFileTextLinks();
        });
        return () => window.cancelAnimationFrame(frame);
    }, [steps, streaming, pendingPermission, fileValidationTick]);

    const patchAggregate = useMemo(() => {
        const entries: {
            summaries: { path: string; added: number; removed: number }[];
            revertPatch?: string;
            diff?: string;
            index: number;
        }[] = [];
        steps.forEach((step, index) => {
            if (step.step_type !== 'observation') return;
            const toolName = String(step.metadata?.tool || '').toLowerCase();
            if (toolName !== 'apply_patch') return;
            const content = String(step.content || '').replace(/\r\n/g, '\n');
            const result = parseApplyPatchResult(content);
            if (!result || !result.ok) return;
            const summaries = (result.summary || [])
                .filter((item) => item && typeof item.path === 'string')
                .map((item) => ({ path: item.path, added: item.added ?? 0, removed: item.removed ?? 0 }));
            entries.push({
                summaries,
                revertPatch: result.revert_patch,
                diff: result.diff || '',
                index
            });
        });
        const fileOrder: string[] = [];
        const fileStats = new Map<string, { added: number; removed: number }>();
        entries.forEach((entry) => {
            entry.summaries.forEach((item) => {
                if (!fileStats.has(item.path)) {
                    fileStats.set(item.path, { added: 0, removed: 0 });
                    fileOrder.push(item.path);
                }
                const stats = fileStats.get(item.path)!;
                stats.added += item.added || 0;
                stats.removed += item.removed || 0;
            });
        });
        const summary = fileOrder.map((path) => ({
            path,
            added: fileStats.get(path)?.added ?? 0,
            removed: fileStats.get(path)?.removed ?? 0
        }));
        const fileGroupsMap = new Map<string, { path: string; added: number; removed: number; diffs: string[] }>();
        summary.forEach((item) => {
            fileGroupsMap.set(item.path, { ...item, diffs: [] });
        });
        entries.forEach((entry) => {
            const fallbackPath = entry.summaries.length === 1 ? entry.summaries[0].path : undefined;
            splitDiffByFile(entry.diff || '', fallbackPath).forEach((chunk) => {
                const key = chunk.path;
                let group = fileGroupsMap.get(key);
                if (!group) {
                    group = { path: key, added: 0, removed: 0, diffs: [] };
                    fileGroupsMap.set(key, group);
                }
                if (chunk.diff.trim()) {
                    group.diffs.push(chunk.diff);
                }
            });
        });
        const fileGroups = Array.from(fileGroupsMap.values());
        const revertPatch = entries
            .map((entry) => entry.revertPatch)
            .filter((patch): patch is string => Boolean(patch))
            .reverse()
            .join('\n');
        return {
            entries,
            summary,
            fileGroups,
            revertPatch,
            patchCount: entries.length,
            fileCount: fileOrder.length
        };
    }, [steps]);

    const handleOpenLink = async (href: string) => {
        if (!href) return;
        const normalized = href.startsWith('www.') ? `https://${href}` : href;
        try {
            await openUrl(normalized);
        } catch {
            // ignore open errors
        }
    };

    const handleOpenFile = async (rawValue: string, line?: number, column?: number) => {
        if (!rawValue) return;
        const parsed = parseFileReference(rawValue);
        if (!parsed?.path) return;
        const normalizedPath = normalizeFileHref(parsed.path);
        const targetLine = parsed.line ?? line;
        const targetColumn = parsed.column ?? column;
        if (onOpenWorkFile) {
            try {
                await onOpenWorkFile(normalizedPath, targetLine, targetColumn);
                return;
            } catch (error) {
                console.error('Failed to open work window for file:', error);
            }
        }
        try {
            await openPath(normalizedPath);
        } catch {
            // ignore open errors
        }
    };

    const handleOpenFileFolder = async (rawValue: string) => {
        if (!rawValue) return;
        const parsed = parseFileReference(rawValue);
        if (!parsed?.path) return;
        const normalizedPath = normalizeFileHref(parsed.path);
        try {
            await revealItemInDir(normalizedPath);
        } catch {
            try {
                const parent = getParentPath(normalizedPath);
                await openPath(parent || normalizedPath);
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
                (() => {
                    const linkStatus = getFileLinkStatus(token.value);
                    const shouldLink = !linkStatus.checkable || linkStatus.status === 'exists';
                    if (!shouldLink) {
                        return <Fragment key={`${token.type}-${idx}`}>{token.value}</Fragment>;
                    }
                    return (
                        <button
                            key={`${token.type}-${idx}`}
                            type="button"
                            className="file-link file-link-pill"
                            onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                handleOpenFile(token.value);
                            }}
                            onContextMenu={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                const parsed = parseFileReference(token.value);
                                if (parsed?.path) {
                                    setFileMenu({ path: normalizeFileHref(parsed.path), x: event.clientX, y: event.clientY });
                                }
                            }}
                        >
                            {token.value}
                        </button>
                    );
                })()
            );
        });
    };

    const renderAstPayload = (payload: AstPayload, stepKey: string, expanded: boolean) => (
        <AstViewer
            payload={payload}
            expanded={expanded}
            rawVisible={!!expandedAstRaw[stepKey]}
            onToggleRaw={() =>
                setExpandedAstRaw((prev) => ({ ...prev, [stepKey]: !prev[stepKey] }))
            }
            onOpenWorkFile={onOpenWorkFile}
        />
    );

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
        if (link && link.href) {
            const rawHref = link.getAttribute('href') || '';
            const href = rawHref || link.href;
            if (isFileHref(rawHref || href)) {
                event.preventDefault();
                event.stopPropagation();
                const parsed = parseFileReference(rawHref || href);
                if (!parsed?.path) return;
                const filePath = normalizeFileHref(parsed.path);
                if (onOpenWorkFile) {
                    void onOpenWorkFile(filePath, parsed.line, parsed.column);
                    return;
                }
                void handleOpenFile(filePath, parsed.line, parsed.column);
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            handleOpenLink(href);
            return;
        }
        const codeEl = target.closest('code');
        if (codeEl && !target.closest('.code-block-container')) {
            const text = codeEl.textContent || '';
            const parsed = parseFileReference(text);
            if (parsed?.path) {
                event.preventDefault();
                event.stopPropagation();
                const filePath = normalizeFileHref(parsed.path);
                if (onOpenWorkFile) {
                    void onOpenWorkFile(filePath, parsed.line, parsed.column);
                    return;
                }
                void handleOpenFile(filePath, parsed.line, parsed.column);
            }
        }
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
            <div className="agent-steps empty" ref={mermaidRootRef}>
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

    const showPatchSummary = !streaming && patchAggregate.summary.length > 0;

    useEffect(() => {
        if (showPatchSummary) {
            setPatchSummaryExpanded(false);
        }
    }, [showPatchSummary]);

    const permissionCard = pendingPermission ? (
        <div className="agent-step tool permission">
            <div className="agent-step-header">
                <span className="agent-step-category">Tool Call</span>
                <span className="agent-step-type">permission</span>
            </div>
            <div className="agent-step-content">
                <div className="permission-card">
                    <div className="permission-title">Shell 权限请求</div>
                    <div className="permission-subtitle">该操作需要权限确认，是否允许执行？</div>
                    <div className="permission-command">{pendingPermission.path}</div>
                    {pendingPermission.reason && (
                        <div className="permission-reason">{pendingPermission.reason}</div>
                    )}
                    <div className="permission-note">仅此一次：只放行本次；始终允许：加入全局允许列表。</div>
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
                            onClick={() => onPermissionDecision && onPermissionDecision('approved_once')}
                            disabled={permissionBusy || !onPermissionDecision}
                            title="仅本次允许，不会加入全局允许列表"
                        >
                            仅此一次
                        </button>
                        <button
                            type="button"
                            className="permission-btn allow"
                            onClick={() => onPermissionDecision && onPermissionDecision('approved')}
                            disabled={permissionBusy || !onPermissionDecision}
                            title="加入全局允许列表，后续不再询问"
                        >
                            始终允许
                        </button>
                    </div>
                </div>
            </div>
        </div>
    ) : null;

    const renderGroupedSteps = () => {
        const items = steps.map((step, index) => {
                const category = STEP_CATEGORY[step.step_type] || 'other';
                const iteration = getIterationValue(step);
                const stepKey = `${step.step_type}-${index}`;
                const isObservation = step.step_type === 'observation';
                const isAction = step.step_type === 'action' || step.step_type === 'action_delta';
                const isError = step.step_type === 'error';
                const isContextCompress = isObservation && Boolean(step.metadata?.context_compress);
                const errorType = String(step.metadata?.error_type || '').toLowerCase();
                const isTimeoutError =
                    isError && (errorType.includes('timeout') || /timeout/i.test(String(step.content || '')));
                const isThought = category === 'thought';
                const rawContent = String(step.content || '');
                const trimmedContent = rawContent.trimStart();
                const lowerContent = trimmedContent.toLowerCase();
                const toolName = String(step.metadata?.tool || '').toLowerCase();
                const isAstObservation = isObservation && toolName === 'code_ast';
                const isRunShell = isObservation && toolName === 'run_shell';
                const looksLikeSearchCall =
                    /\bsearch\s*\[/i.test(rawContent) || /\bsearch\s*\(/i.test(rawContent) || lowerContent.startsWith('search');
                const canTranslateSearch = isAction && (toolName === 'search' || looksLikeSearchCall) && hasUnicodeEscapes(rawContent);
                const isTranslated = !!translatedSearchSteps[stepKey];
                const baseContent = canTranslateSearch && isTranslated ? decodeUnicodeEscapes(rawContent) : rawContent;
                const finalAnswerFromJson = category === 'final' ? extractJsonFinalAnswer(baseContent) : null;
                const displayContent = finalAnswerFromJson ?? baseContent;
                const errorDetails = isError ? String(step.metadata?.traceback || '') : '';
                const hasErrorDetails = Boolean(errorDetails);
                const errorExpanded = !!expandedErrors[stepKey];
                let observationText = '';
                let observationPreview = '';
                let observationHasMore = false;
                let observationIsDiff = false;
                let observationFailed = false;
                let applyPatchResult: ApplyPatchResult | null = null;
                let isApplyPatch = false;
                let astPayload: AstPayload | null = null;
                let shellPayload: ReturnType<typeof parseShellOutput> | null = null;
                let shellBody = '';
                let shellPreview = '';
                if (isObservation) {
                    observationText = rawContent.replace(/\r\n/g, '\n');
                    const exitCode = parseExitCode(observationText);
                    if (exitCode !== null && exitCode !== 0) {
                        observationFailed = true;
                    }
                    const lines = observationText.split('\n');
                    observationHasMore = lines.length > 1;
                    observationPreview = observationHasMore ? `${lines[0]} ...` : lines[0] || '';
                    observationIsDiff = looksLikeDiff(observationText);
                    isApplyPatch = toolName === 'apply_patch';
                    if (isApplyPatch) {
                        applyPatchResult = parseApplyPatchResult(observationText);
                        if (applyPatchResult && applyPatchResult.ok === false) {
                            observationFailed = true;
                        }
                    } else if (isAstObservation) {
                        astPayload = parseAstPayload(observationText);
                    } else if (isRunShell) {
                        shellPayload = parseShellOutput(observationText);
                        if (shellPayload) {
                            shellBody = shellPayload.body || '';
                            const bodyLines = shellBody.split('\n');
                            observationHasMore = bodyLines.length > 1;
                            shellPreview = observationHasMore ? `${bodyLines[0]} ...` : bodyLines[0] || '';
                            observationPreview = shellPreview;
                            observationIsDiff = false;
                            if (typeof shellPayload.header.exit_code === 'number' && shellPayload.header.exit_code !== 0) {
                                observationFailed = true;
                            }
                        }
                    }
                }
                const patchExpandedDefault = patchAggregate.patchCount > 1;
                const patchExpanded = isApplyPatch ? expandedPatches[stepKey] ?? patchExpandedDefault : false;
                const showObservationToggle = isObservation && observationHasMore && !isApplyPatch;
                const isObservationExpanded = isAstObservation ? (expandedObservations[stepKey] ?? true) : !!expandedObservations[stepKey];

                return {
                    iteration,
                    element: (
                        <Fragment key={`${step.step_type}-${index}`}>
                        <div className={`agent-step ${category}${isAction ? ' action' : ''}${isContextCompress ? ' compression' : ''}`}>
                            <div className="agent-step-header">
                                    <span className="agent-step-category">{CATEGORY_LABELS[category]}</span>
                                    <div className="agent-step-header-actions">
                                        {isContextCompress && (
                                            <span className="context-compress-badge">
                                                {step.metadata?.current_turn ? '本轮压缩' : '上下文压缩'}
                                            </span>
                                        )}
                                        {isObservation && observationFailed && (
                                            <span className="agent-step-failure-icon" title="Failed">X</span>
                                        )}
                                        <span className="agent-step-type">{step.step_type}</span>
                                        {showObservationToggle && (
                                            <button
                                                type="button"
                                                className="observation-toggle"
                                                aria-label={isObservationExpanded ? 'Collapse observation' : 'Expand observation'}
                                                title={isObservationExpanded ? 'Collapse' : 'Expand'}
                                            onClick={() =>
                                                setExpandedObservations((prev) => {
                                                    const current = isAstObservation ? (prev[stepKey] ?? true) : !!prev[stepKey];
                                                    return { ...prev, [stepKey]: !current };
                                                })
                                            }
                                        >
                                            <span aria-hidden="true">
                                                {isObservationExpanded ? '^' : 'v'}
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
                                <div className={`agent-step-content observation${isContextCompress ? ' compression' : ''}`}>
                                    {shellPayload && isRunShell ? (
                                        <div className="shell-card">
                                            <div className="shell-card-header">
                                                <span className="shell-card-title">Shell</span>
                                                <div className="shell-card-badges">
                                                    <span className={`shell-badge ${shellPayload.header.pty ? 'ok' : ''}`}>
                                                        {shellPayload.header.pty ? 'PTY' : 'PIPE'}
                                                    </span>
                                                    {shellPayload.header.status && (
                                                        <span className="shell-badge">status: {shellPayload.header.status}</span>
                                                    )}
                                                    {typeof shellPayload.header.exit_code === 'number' && (
                                                        <span
                                                            className={`shell-badge ${shellPayload.header.exit_code === 0 ? 'ok' : 'err'}`}
                                                        >
                                                            exit {shellPayload.header.exit_code}
                                                        </span>
                                                    )}
                                                    {shellPayload.header.pty_fallback && (
                                                        <span className="shell-badge warn">pty fallback</span>
                                                    )}
                                                </div>
                                            </div>
                                            <div className="shell-card-meta">
                                                {shellPayload.header.pty_id && (
                                                    <div className="shell-meta-item">
                                                        <span className="shell-meta-label">pty_id</span>
                                                        <span className="shell-meta-value">{shellPayload.header.pty_id}</span>
                                                    </div>
                                                )}
                                                {typeof shellPayload.header.cursor === 'number' && (
                                                    <div className="shell-meta-item">
                                                        <span className="shell-meta-label">cursor</span>
                                                        <span className="shell-meta-value">{shellPayload.header.cursor}</span>
                                                    </div>
                                                )}
                                                {typeof shellPayload.header.idle_timeout === 'number' && (
                                                    <div className="shell-meta-item">
                                                        <span className="shell-meta-label">idle_timeout</span>
                                                        <span className="shell-meta-value">{shellPayload.header.idle_timeout}ms</span>
                                                    </div>
                                                )}
                                                {typeof shellPayload.header.buffer_size === 'number' && (
                                                    <div className="shell-meta-item">
                                                        <span className="shell-meta-label">buffer</span>
                                                        <span className="shell-meta-value">{shellPayload.header.buffer_size}</span>
                                                    </div>
                                                )}
                                                {typeof shellPayload.header.reset === 'boolean' && (
                                                    <div className="shell-meta-item">
                                                        <span className="shell-meta-label">reset</span>
                                                        <span className="shell-meta-value">{String(shellPayload.header.reset)}</span>
                                                    </div>
                                                )}
                                            </div>
                                            <div className={`shell-card-output${isObservationExpanded ? ' expanded' : ''}`}>
                                                {renderLinkedText(
                                                    (isObservationExpanded ? shellBody : shellPreview) || '(no output)'
                                                )}
                                            </div>
                                        </div>
                                    ) : isApplyPatch && applyPatchResult ? (
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
                                                                    [stepKey]: !(prev[stepKey] ?? true)
                                                                }))
                                                            }
                                                        >
                                                            {patchExpanded ? '收起' : '展开'}
                                                        </button>
                                                    </div>
                                                </div>
                                                <div className="patch-summary-list">
                                                    {(applyPatchResult.summary || []).length === 0 && (
                                                        <div className="patch-summary-item">
                                                            <span className="patch-summary-path">无变更</span>
                                                            <span className="patch-summary-counts">
                                                                <span className="patch-count plus">+0</span>
                                                                <span className="patch-count minus">-0</span>
                                                            </span>
                                                        </div>
                                                    )}
                                                    {(applyPatchResult.summary || []).map((item, idx) => (
                                                        <div key={`${item.path}-${idx}`} className="patch-summary-item">
                                                            {onOpenWorkFile ? (
                                                                <button
                                                                    type="button"
                                                                    className="patch-summary-path clickable"
                                                                    onClick={(event) => {
                                                                        event.preventDefault();
                                                                        event.stopPropagation();
                                                                        void onOpenWorkFile(item.path);
                                                                    }}
                                                                >
                                                                    {item.path}
                                                                </button>
                                                            ) : (
                                                                <span className="patch-summary-path">{item.path}</span>
                                                            )}
                                                            <span className="patch-summary-counts">
                                                                <span className="patch-count plus">+{item.added}</span>
                                                                <span className="patch-count minus">-{item.removed}</span>
                                                            </span>
                                                        </div>
                                                    ))}
                                                </div>
                                                {patchExpanded && applyPatchResult.diff && (
                                                    <DiffView content={applyPatchResult.diff} />
                                                )}
                                            </div>
                                        ) : (
                                            <div className="observation-text">{applyPatchResult.error || 'Apply patch failed.'}</div>
                                        )
                                    ) : isObservationExpanded ? (
                                        isAstObservation && astPayload ? (
                                            renderAstPayload(astPayload, stepKey, isObservationExpanded)
                                        ) : observationIsDiff ? (
                                            <DiffView content={observationText} />
                                        ) : (
                                            <div className="observation-text expanded">{renderLinkedText(observationText)}</div>
                                        )
                                    ) : (
                                        isAstObservation && astPayload ? (
                                            renderAstPayload(astPayload, stepKey, isObservationExpanded)
                                        ) : (
                                            <div className="observation-text">{renderLinkedText(observationPreview)}</div>
                                        )
                                    )}
                                </div>
                            ) : (
                                <div
                                    className={`agent-step-content${isAction ? ' action-content' : ''}`}
                                    ref={isThought ? (el) => { thoughtRefs.current[stepKey] = el; } : undefined}
                                    onScroll={isThought ? handleThoughtScroll(stepKey) : undefined}
                                >
                                    {isAction ? (
                                        <div className="action-raw">{displayContent}</div>
                                    ) : (
                                        <RichContent
                                            content={displayContent}
                                            onClick={(event) => {
                                                handleMarkdownClick(event);
                                                handleContentClick(event);
                                            }}
                                        />
                                    )}
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
                    )
                };
            });
        return groupStepElements(items, { debugActive, onOpenDebugCall });
    };

    return (
        <>
            <div className="agent-steps" ref={mermaidRootRef}>
            {renderGroupedSteps()}
            {showPatchSummary && (
                <div className="agent-step tool">
                    <div className="agent-step-header">
                        <span className="agent-step-category">Tool Call</span>
                        <div className="agent-step-header-actions">
                            <span className="agent-step-type">修改总结</span>
                        </div>
                    </div>
                    <div className="agent-step-content observation">
                        <div className="patch-result">
                            <div className="patch-summary-header">
                                <span className="patch-summary-title">修改总结</span>
                                <div className="patch-summary-actions">
                                    <button
                                        type="button"
                                        className="patch-summary-btn simple"
                                        onClick={() => setPatchSummaryExpanded((prev) => !prev)}
                                    >
                                        {patchSummaryExpanded ? '收起' : '展开'}
                                    </button>
                                    {onRevertPatch && patchAggregate.revertPatch && (
                                        <button
                                            type="button"
                                            className="patch-summary-btn simple danger"
                                            onClick={() => onRevertPatch(patchAggregate.revertPatch, messageId)}
                                            disabled={patchRevertBusy}
                                        >
                                            {patchRevertBusy ? '处理中...' : '撤销'}
                                        </button>
                                    )}
                                </div>
                            </div>
                                    {!patchSummaryExpanded ? (
                                        <div className="patch-summary-list">
                                            {patchAggregate.summary.map((item) => (
                                                <div key={`patch-summary-${item.path}`} className="patch-summary-item">
                                                    {onOpenWorkFile ? (
                                                        <button
                                                            type="button"
                                                            className="patch-summary-path clickable"
                                                            onClick={(event) => {
                                                                event.preventDefault();
                                                                event.stopPropagation();
                                                                void onOpenWorkFile(item.path);
                                                            }}
                                                        >
                                                            {item.path}
                                                        </button>
                                                    ) : (
                                                        <span className="patch-summary-path">{item.path}</span>
                                                    )}
                                                    <span className="patch-summary-counts">
                                                        <span className="patch-count plus">+{item.added}</span>
                                                        <span className="patch-count minus">-{item.removed}</span>
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <>
                                    <div className="patch-summary-list">
                                        {patchAggregate.summary.map((item) => (
                                            <div key={`patch-summary-${item.path}`} className="patch-summary-item">
                                                {onOpenWorkFile ? (
                                                    <button
                                                        type="button"
                                                        className="patch-summary-path clickable"
                                                        onClick={(event) => {
                                                            event.preventDefault();
                                                            event.stopPropagation();
                                                            void onOpenWorkFile(item.path);
                                                        }}
                                                    >
                                                        {item.path}
                                                    </button>
                                                ) : (
                                                    <span className="patch-summary-path">{item.path}</span>
                                                )}
                                            <span className="patch-summary-counts">
                                                <span className="patch-count plus">+{item.added}</span>
                                                <span className="patch-count minus">-{item.removed}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                                    {patchAggregate.fileGroups
                                        .filter((group) => group.diffs.length > 0)
                                        .map((group) => (
                                            <div key={`patch-detail-${group.path}`} className="patch-detail">
                                                <div className="patch-detail-header">
                                                    <span className="patch-detail-path">{group.path}</span>
                                                    <span className="patch-detail-counts">
                                                        <span className="patch-count plus">+{group.added}</span>
                                                        <span className="patch-count minus">-{group.removed}</span>
                                                    </span>
                                                </div>
                                                {group.diffs.map((diff, idx) => (
                                                    <DiffView key={`patch-detail-${group.path}-${idx}`} content={diff} />
                                                ))}
                                            </div>
                                        ))}
                                </>
                            )}
                        </div>
                    </div>
                </div>
            )}
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
