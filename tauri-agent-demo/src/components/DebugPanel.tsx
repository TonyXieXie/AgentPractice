import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { runAstTool, getAstCache, getAstCacheFile, getCodeMap } from '../api';
import { Message, LLMCall, AstPayload, AstRequest, AgentMode, AstCacheResponse, AstCacheFile, CodeMapResponse } from '../types';
import AstViewer from './AstViewer';
import './DebugPanel.css';

interface DebugPanelProps {
    messages: Message[];
    llmCalls: LLMCall[];
    onClose: () => void;
    focusTarget?: { key: string; messageId?: number; iteration?: number; callId?: number } | null;
    currentSessionId?: string | null;
    workPath?: string | null;
    extraWorkPaths?: string[] | null;
    agentMode?: AgentMode;
    onOpenWorkFile?: (filePath: string, line?: number, column?: number) => void;
}

function DebugPanel({
    messages,
    llmCalls,
    onClose,
    focusTarget,
    currentSessionId,
    workPath,
    extraWorkPaths,
    agentMode,
    onOpenWorkFile
}: DebugPanelProps) {
    const [expandedMessageId, setExpandedMessageId] = useState<string | null>(null);
    const [expandedCallId, setExpandedCallId] = useState<string | null>(null);
    const [rawStreamVisible, setRawStreamVisible] = useState<Record<string, boolean>>({});
    const [panelWidth, setPanelWidth] = useState(400);
    const [focusedCallId, setFocusedCallId] = useState<number | null>(null);
    const [activeTab, setActiveTab] = useState<'llm' | 'ast' | 'cache'>('llm');
    const [astForm, setAstForm] = useState({
        path: '',
        mode: 'outline',
        language: '',
        extensions: '',
        maxFiles: '',
        maxSymbols: '',
        maxNodes: '',
        maxDepth: '',
        maxBytes: '',
        includePositions: true,
        includeText: false
    });
    const [astResult, setAstResult] = useState<AstPayload | null>(null);
    const [astError, setAstError] = useState<string | null>(null);
    const [astLoading, setAstLoading] = useState(false);
    const [astDurationMs, setAstDurationMs] = useState<number | null>(null);
    const [astRawVisible, setAstRawVisible] = useState(false);
    const [cacheRoot, setCacheRoot] = useState(workPath || '');
    const [cacheSummary, setCacheSummary] = useState<AstCacheResponse | null>(null);
    const [cacheError, setCacheError] = useState<string | null>(null);
    const [cacheLoading, setCacheLoading] = useState(false);
    const [cacheDurationMs, setCacheDurationMs] = useState<number | null>(null);
    const [cacheFile, setCacheFile] = useState<AstPayload | null>(null);
    const [cacheFilePath, setCacheFilePath] = useState<string | null>(null);
    const [cacheRawVisible, setCacheRawVisible] = useState(false);
    const [codeMapText, setCodeMapText] = useState<string>('');
    const [codeMapLoading, setCodeMapLoading] = useState(false);
    const [codeMapError, setCodeMapError] = useState<string | null>(null);
    const [codeMapDurationMs, setCodeMapDurationMs] = useState<number | null>(null);
    const [codeMapUpdatedAt, setCodeMapUpdatedAt] = useState<number | null>(null);
    const panelRef = useRef<HTMLDivElement>(null);

    const toggleExpandMessage = (id: string) => {
        setExpandedMessageId(expandedMessageId === id ? null : id);
    };

    const toggleExpandCall = (id: string) => {
        setExpandedCallId(expandedCallId === id ? null : id);
    };
    const handleResizeStart = (event: ReactMouseEvent) => {
        event.preventDefault();
        const startX = event.clientX;
        const startWidth = panelRef.current?.getBoundingClientRect().width ?? panelWidth;

        const handleMouseMove = (moveEvent: MouseEvent) => {
            const delta = startX - moveEvent.clientX;
            const maxWidth = Math.min(window.innerWidth * 0.8, window.innerWidth - 240);
            const nextWidth = Math.max(320, Math.min(maxWidth, startWidth + delta));
            setPanelWidth(nextWidth);
        };

        const handleMouseUp = () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
    };

    const toggleRawStream = (id: string) => {
        setRawStreamVisible((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    const handleUseWorkDir = () => {
        if (!workPath) return;
        setAstForm((prev) => ({ ...prev, path: workPath }));
    };

    const parseNumberField = (value: string) => {
        const trimmed = value.trim();
        if (!trimmed) return undefined;
        const parsed = Number(trimmed);
        if (!Number.isFinite(parsed)) return undefined;
        return Math.max(0, Math.floor(parsed));
    };

    const parseExtensions = (value: string) => {
        const trimmed = value.trim();
        if (!trimmed) return undefined;
        const parts = trimmed.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
        return parts.length > 0 ? parts : undefined;
    };

    const formatDuration = (value?: number | null) => {
        if (value == null || !Number.isFinite(value)) return '';
        if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
        return `${Math.round(value)}ms`;
    };

    const buildAstRequest = (): AstRequest | null => {
        const path = astForm.path.trim();
        if (!path) return null;
        const payload: AstRequest = {
            path,
            mode: astForm.mode === 'full' ? 'full' : 'outline',
            include_positions: astForm.includePositions,
            include_text: astForm.includeText
        };
        const language = astForm.language.trim();
        if (language) payload.language = language;
        const extensions = parseExtensions(astForm.extensions);
        if (extensions) payload.extensions = extensions;
        const maxFiles = parseNumberField(astForm.maxFiles);
        if (maxFiles !== undefined) payload.max_files = maxFiles;
        const maxSymbols = parseNumberField(astForm.maxSymbols);
        if (maxSymbols !== undefined) payload.max_symbols = maxSymbols;
        const maxNodes = parseNumberField(astForm.maxNodes);
        if (maxNodes !== undefined) payload.max_nodes = maxNodes;
        const maxDepth = parseNumberField(astForm.maxDepth);
        if (maxDepth !== undefined) payload.max_depth = maxDepth;
        const maxBytes = parseNumberField(astForm.maxBytes);
        if (maxBytes !== undefined) payload.max_bytes = maxBytes;
        if (currentSessionId) payload.session_id = currentSessionId;
        if (workPath) payload.work_path = workPath;
        if (extraWorkPaths && extraWorkPaths.length > 0) payload.extra_work_paths = extraWorkPaths;
        if (agentMode) payload.agent_mode = agentMode;
        return payload;
    };

    const handleRunAst = async () => {
        const payload = buildAstRequest();
        if (!payload) {
            setAstError('Please enter a path.');
            return;
        }
        const start = performance.now();
        setAstLoading(true);
        setAstError(null);
        setAstDurationMs(null);
        try {
            const result = await runAstTool(payload);
            setAstResult(result as AstPayload);
            setAstRawVisible(false);
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to run AST.';
            setAstError(message);
        } finally {
            setAstLoading(false);
            setAstDurationMs(performance.now() - start);
        }
    };

    const handleClearAst = () => {
        setAstResult(null);
        setAstError(null);
        setAstRawVisible(false);
    };

    const formatEpochTime = (value?: number) => {
        if (!value) return '';
        const date = new Date(value * 1000);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
    };

    const formatTimestamp = (value?: number | null) => {
        if (!value) return '';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
    };

    const loadCacheSummary = async (root: string) => {
        if (!root) return;
        const start = performance.now();
        setCacheLoading(true);
        setCacheError(null);
        setCacheDurationMs(null);
        try {
            const result = await getAstCache(root);
            setCacheSummary(result as AstCacheResponse);
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to fetch AST cache.';
            setCacheError(message);
        } finally {
            setCacheLoading(false);
            setCacheDurationMs(performance.now() - start);
        }
    };

    const loadCacheFile = async (root: string, path: string) => {
        if (!root || !path) return;
        setCacheLoading(true);
        setCacheError(null);
        try {
            const result = await getAstCacheFile(root, path);
            setCacheFile(result as AstPayload);
            setCacheFilePath(path);
            setCacheRawVisible(false);
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to fetch AST cache file.';
            setCacheError(message);
        } finally {
            setCacheLoading(false);
        }
    };

    const loadCodeMap = async (root: string) => {
        if (!root) return;
        if (!currentSessionId) {
            setCodeMapError('No active session.');
            return;
        }
        const start = performance.now();
        setCodeMapLoading(true);
        setCodeMapError(null);
        setCodeMapDurationMs(null);
        try {
            const result = await getCodeMap(currentSessionId, root);
            const payload = result as CodeMapResponse;
            setCodeMapText(payload.prompt || '');
            setCodeMapUpdatedAt(Date.now());
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to fetch code map.';
            setCodeMapError(message);
        } finally {
            setCodeMapLoading(false);
            setCodeMapDurationMs(performance.now() - start);
        }
    };

    useEffect(() => {
        if (activeTab !== 'cache') return;
        if (!cacheRoot) return;
        void loadCacheSummary(cacheRoot);
        void loadCodeMap(cacheRoot);
    }, [activeTab, cacheRoot, currentSessionId]);

    useEffect(() => {
        if (!cacheRoot) {
            setCacheSummary(null);
            setCacheFile(null);
            setCacheFilePath(null);
            setCodeMapText('');
            setCacheError(null);
            setCodeMapError(null);
            setCodeMapUpdatedAt(null);
        }
    }, [cacheRoot]);

    useEffect(() => {
        if (!focusTarget) return;
        const { callId, messageId, iteration } = focusTarget;
        let targetCall: LLMCall | undefined;
        if (typeof callId === 'number') {
            targetCall = llmCalls.find((item) => item.id === callId);
        }
        if (!targetCall && typeof messageId === 'number' && typeof iteration === 'number') {
            targetCall = llmCalls.find((item) => item.message_id === messageId && item.iteration === iteration);
        }
        if (!targetCall) return;
        const messageKey = typeof targetCall.message_id === 'number' ? `msg-${targetCall.message_id}` : null;
        if (messageKey) {
            setExpandedMessageId(messageKey);
        }
        const callKey = `call-${targetCall.id}`;
        setExpandedCallId(callKey);
        setFocusedCallId(targetCall.id);

        const attemptScroll = () => {
            const container = panelRef.current;
            if (!container) return false;
            const node = container.querySelector<HTMLElement>(`[data-call-id="${targetCall.id}"]`);
            if (node && 'scrollIntoView' in node) {
                node.scrollIntoView({ behavior: 'smooth', block: 'start' });
                return true;
            }
            return false;
        };

        if (!attemptScroll()) {
            window.setTimeout(() => {
                attemptScroll();
            }, 60);
        }
    }, [focusTarget, llmCalls]);

    useEffect(() => {
        if (focusTarget) {
            setActiveTab('llm');
        }
    }, [focusTarget]);

    useEffect(() => {
        if (workPath && !cacheRoot) {
            setCacheRoot(workPath);
        }
    }, [workPath, cacheRoot]);

    const copyToClipboard = async (text: string) => {
        try {
            await navigator.clipboard.writeText(text);
            alert('Copied to clipboard.');
        } catch (error) {
            console.error('Copy failed:', error);
            alert('Copy failed.');
        }
    };

    const formatJson = (obj: any) => JSON.stringify(obj, null, 2);

    const formatTime = (value?: string) => {
        if (!value) return '';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    };

    const getTokenUsage = (raw: any) => {
        if (!raw || !raw.usage) return null;
        const usage = raw.usage;
        return {
            prompt: usage.prompt_tokens || 0,
            completion: usage.completion_tokens || 0,
            total: usage.total_tokens || 0,
        };
    };

    const renderJsonSection = (title: string, data: any) => {
        if (!data) return null;
        const text = formatJson(data);
        return (
            <div className="debug-section">
                <div className="section-header">
                    <h4>{title}</h4>
                    <button className="copy-btn" onClick={() => copyToClipboard(text)}>
                        Copy
                    </button>
                </div>
                <pre className="json-viewer">{text}</pre>
            </div>
        );
    };

    const renderTextSection = (title: string, data?: string | null) => {
        if (!data) return null;
        return (
            <div className="debug-section">
                <div className="section-header">
                    <h4>{title}</h4>
                    <button className="copy-btn" onClick={() => copyToClipboard(data)}>
                        Copy
                    </button>
                </div>
                <pre className="json-viewer text-viewer">{data}</pre>
            </div>
        );
    };

    const callsByMessage = useMemo(() => {
        const map = new Map<number, LLMCall[]>();
        const orphan: LLMCall[] = [];
        for (const call of llmCalls) {
            if (typeof call.message_id === 'number') {
                if (!map.has(call.message_id)) {
                    map.set(call.message_id, []);
                }
                map.get(call.message_id)!.push(call);
            } else {
                orphan.push(call);
            }
        }
        return { map, orphan };
    }, [llmCalls]);

    const renderLLMCall = (call: LLMCall) => {
        const callKey = `call-${call.id}`;
        const usage = getTokenUsage(call.response_json);
        const hasRawEvents = Boolean(call.response_json && (call.response_json as any).events);
        const hasProcessed = Boolean(call.processed_json);
        const showRawStream = Boolean(rawStreamVisible[callKey]);
        const isFocused = focusedCallId === call.id;
        return (
            <div key={call.id} className={`debug-message${isFocused ? ' focused' : ''}`} data-call-id={call.id}>
                <div className="debug-message-header" onClick={() => toggleExpandCall(callKey)}>
                    <div className="message-title">
                        <span className="role-badge call">LLM</span>
                        <span className="message-id">Call {call.id}</span>
                        <span className="message-time">{formatTime(call.created_at)}</span>
                    </div>
                    <button className="expand-btn">{expandedCallId === callKey ? '-' : '+'}</button>
                </div>

                {expandedCallId === callKey && (
                    <div className="debug-message-body">
                        <div className="debug-meta">
                            <span className="meta-pill">agent: {call.agent_type || 'unknown'}</span>
                            <span className="meta-pill">iteration: {call.iteration ?? 0}</span>
                            <span className="meta-pill">stream: {call.stream ? 'yes' : 'no'}</span>
                            <span className="meta-pill">profile: {call.api_profile || 'unknown'}</span>
                            <span className="meta-pill">format: {call.api_format || 'unknown'}</span>
                            <span className="meta-pill">model: {call.model || 'unknown'}</span>
                        </div>

                        {renderJsonSection('Request JSON', call.request_json)}
                        {hasRawEvents && hasProcessed ? (
                            <div className="debug-section">
                                <div className="section-header">
                                    <h4>Response JSON (processed)</h4>
                                    <div className="section-actions">
                                        <button className="copy-btn" onClick={() => copyToClipboard(formatJson(call.processed_json))}>
                                            Copy
                                        </button>
                                        <button className="copy-btn" onClick={() => toggleRawStream(callKey)}>
                                            {showRawStream ? 'Hide raw stream' : 'Show raw stream'}
                                        </button>
                                    </div>
                                </div>
                                <pre className="json-viewer">{formatJson(call.processed_json)}</pre>
                                {showRawStream && (
                                    <div className="debug-section">
                                        <div className="section-header">
                                            <h4>Response JSON (raw stream)</h4>
                                            <button className="copy-btn" onClick={() => copyToClipboard(formatJson(call.response_json))}>
                                                Copy
                                            </button>
                                        </div>
                                        <pre className="json-viewer">{formatJson(call.response_json)}</pre>
                                    </div>
                                )}
                            </div>
                        ) : (
                            renderJsonSection('Response JSON', call.response_json)
                        )}
                        {renderTextSection('Response Text', call.response_text)}
                        {!hasRawEvents && renderJsonSection('Processed JSON', call.processed_json)}

                        {usage && (
                            <div className="token-usage">
                                <h5>Token Usage</h5>
                                <div className="token-stats">
                                    <span>Prompt: {usage.prompt}</span>
                                    <span>Completion: {usage.completion}</span>
                                    <span>Total: {usage.total}</span>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="debug-panel" ref={panelRef} style={{ width: `${panelWidth}px` }}>
            <div className="debug-resize-handle" onMouseDown={handleResizeStart} />
            <div className="debug-header">
                <div className="debug-title">
                    <h2>Debug</h2>
                    <div className="debug-tabs">
                        <button
                            type="button"
                            className={`debug-tab${activeTab === 'llm' ? ' active' : ''}`}
                            onClick={() => setActiveTab('llm')}
                        >
                            LLM
                        </button>
                        <button
                            type="button"
                            className={`debug-tab${activeTab === 'ast' ? ' active' : ''}`}
                            onClick={() => setActiveTab('ast')}
                        >
                            AST
                        </button>
                        <button
                            type="button"
                            className={`debug-tab${activeTab === 'cache' ? ' active' : ''}`}
                            onClick={() => setActiveTab('cache')}
                        >
                            Cache
                        </button>
                    </div>
                </div>
                <button className="close-btn" onClick={onClose}>
                    X
                </button>
            </div>

            <div className="debug-content">
                {activeTab === 'cache' ? (
                    <div className="ast-cache">
                        <div className="ast-debug-form">
                            <div className="ast-debug-row">
                                <label>Root</label>
                                <div className="ast-debug-input-row">
                                    <input
                                        className="ast-debug-input"
                                        value={cacheRoot}
                                        onChange={(event) => setCacheRoot(event.target.value)}
                                        placeholder="work path"
                                    />
                                    {workPath && (
                                        <button
                                            type="button"
                                            className="debug-mini-btn"
                                            onClick={() => setCacheRoot(workPath)}
                                        >
                                            Use work dir
                                        </button>
                                    )}
                                </div>
                            </div>
                            <div className="ast-debug-actions">
                                <button
                                    type="button"
                                    className="debug-btn"
                                    onClick={() => cacheRoot && loadCacheSummary(cacheRoot)}
                                    disabled={cacheLoading || !cacheRoot}
                                >
                                    {cacheLoading ? 'Loading...' : 'Refresh cache'}
                                </button>
                                <button
                                    type="button"
                                    className="debug-btn ghost"
                                    onClick={() => cacheRoot && loadCodeMap(cacheRoot)}
                                    disabled={codeMapLoading || !cacheRoot}
                                >
                                    {codeMapLoading ? 'Loading...' : 'Load code map'}
                                </button>
                                {cacheDurationMs != null && (
                                    <span className="ast-debug-timing">cache {formatDuration(cacheDurationMs)}</span>
                                )}
                                {codeMapDurationMs != null && (
                                    <span className="ast-debug-timing">code map {formatDuration(codeMapDurationMs)}</span>
                                )}
                            </div>
                        </div>

                        {cacheError && <div className="ast-error">{cacheError}</div>}

                        <div className="ast-cache-section">
                            <div className="ast-cache-title">
                                AST Cache
                                {cacheSummary?.files && (
                                    <span className="ast-cache-count">{cacheSummary.files.length} files</span>
                                )}
                            </div>
                            {!cacheSummary?.files || cacheSummary.files.length === 0 ? (
                                <div className="ast-empty">No cached files.</div>
                            ) : (
                                <div className="ast-cache-list">
                                    {cacheSummary.files.map((file) => {
                                        const entry = file as AstCacheFile;
                                        const isActive = cacheFilePath === entry.path;
                                        return (
                                            <button
                                                key={entry.path}
                                                type="button"
                                                className={`ast-cache-row${entry.stale ? ' stale' : ''}${isActive ? ' active' : ''}`}
                                                onClick={() => loadCacheFile(cacheRoot, entry.path)}
                                            >
                                                <span className="ast-cache-path">{entry.path}</span>
                                                <span className="ast-cache-meta">
                                                    parsed {formatEpochTime(entry.parsed_at)} · mtime {formatEpochTime(entry.file_mtime)}
                                                    {entry.stale ? ' · stale' : ''}
                                                </span>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>

                        {cacheFile && (
                            <AstViewer
                                payload={cacheFile}
                                expanded
                                rawVisible={cacheRawVisible}
                                onToggleRaw={() => setCacheRawVisible((prev) => !prev)}
                                onOpenWorkFile={onOpenWorkFile}
                            />
                        )}

                        <div className="ast-cache-section">
                            <div className="ast-cache-title">Code Map</div>
                            {codeMapError && <div className="ast-error">{codeMapError}</div>}
                            {codeMapUpdatedAt && (
                                <div className="ast-cache-meta">last refresh {formatTimestamp(codeMapUpdatedAt)}</div>
                            )}
                            {codeMapText ? (
                                <pre className="json-viewer text-viewer">{codeMapText}</pre>
                            ) : (
                                <div className="ast-empty">No code map available.</div>
                            )}
                        </div>
                    </div>
                ) : activeTab === 'ast' ? (
                    <div className="ast-debug">
                        <form
                            className="ast-debug-form"
                            onSubmit={(event) => {
                                event.preventDefault();
                                void handleRunAst();
                            }}
                        >
                            <div className="ast-debug-row">
                                <label>Path</label>
                                <div className="ast-debug-input-row">
                                    <input
                                        className="ast-debug-input"
                                        value={astForm.path}
                                        onChange={(event) => setAstForm((prev) => ({ ...prev, path: event.target.value }))}
                                        placeholder="e.g. src/main.cpp or src/"
                                    />
                                    {workPath && (
                                        <button type="button" className="debug-mini-btn" onClick={handleUseWorkDir}>
                                            Use work dir
                                        </button>
                                    )}
                                </div>
                                <div className="ast-debug-hint">
                                    Relative paths resolve from the current work path.
                                </div>
                            </div>

                            <div className="ast-debug-grid">
                                <div className="ast-debug-field">
                                    <label>Mode</label>
                                    <select
                                        className="ast-debug-select"
                                        value={astForm.mode}
                                        onChange={(event) => setAstForm((prev) => ({ ...prev, mode: event.target.value }))}
                                    >
                                        <option value="outline">outline</option>
                                        <option value="full">full</option>
                                    </select>
                                </div>
                                <div className="ast-debug-field">
                                    <label>Language</label>
                                    <input
                                        className="ast-debug-input"
                                        value={astForm.language}
                                        onChange={(event) => setAstForm((prev) => ({ ...prev, language: event.target.value }))}
                                        placeholder="auto"
                                    />
                                </div>
                            </div>

                            <details className="ast-debug-advanced">
                                <summary>Advanced</summary>
                                <div className="ast-debug-grid">
                                    <div className="ast-debug-field">
                                        <label>Extensions</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.extensions}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, extensions: event.target.value }))}
                                            placeholder=".ts,.tsx,.cpp"
                                        />
                                    </div>
                                    <div className="ast-debug-field">
                                        <label>Max files</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.maxFiles}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, maxFiles: event.target.value }))}
                                            placeholder="default 50"
                                        />
                                    </div>
                                    <div className="ast-debug-field">
                                        <label>Max symbols</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.maxSymbols}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, maxSymbols: event.target.value }))}
                                            placeholder="default 2000"
                                        />
                                    </div>
                                    <div className="ast-debug-field">
                                        <label>Max nodes</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.maxNodes}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, maxNodes: event.target.value }))}
                                            placeholder="default 2000"
                                        />
                                    </div>
                                    <div className="ast-debug-field">
                                        <label>Max depth</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.maxDepth}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, maxDepth: event.target.value }))}
                                            placeholder="default 12"
                                        />
                                    </div>
                                    <div className="ast-debug-field">
                                        <label>Max bytes</label>
                                        <input
                                            className="ast-debug-input"
                                            value={astForm.maxBytes}
                                            onChange={(event) => setAstForm((prev) => ({ ...prev, maxBytes: event.target.value }))}
                                            placeholder="default 200000"
                                        />
                                    </div>
                                </div>
                                <div className="ast-debug-checks">
                                    <label className="ast-debug-check">
                                        <input
                                            type="checkbox"
                                            checked={astForm.includePositions}
                                            onChange={(event) =>
                                                setAstForm((prev) => ({ ...prev, includePositions: event.target.checked }))
                                            }
                                        />
                                        Include positions
                                    </label>
                                    <label className="ast-debug-check">
                                        <input
                                            type="checkbox"
                                            checked={astForm.includeText}
                                            onChange={(event) =>
                                                setAstForm((prev) => ({ ...prev, includeText: event.target.checked }))
                                            }
                                        />
                                        Include text (full mode)
                                    </label>
                                </div>
                            </details>

                            <div className="ast-debug-actions">
                                <button type="submit" className="debug-btn" disabled={astLoading}>
                                    {astLoading ? 'Running...' : 'Run AST'}
                                </button>
                                <button type="button" className="debug-btn ghost" onClick={handleClearAst}>
                                    Clear
                                </button>
                                {astDurationMs != null && (
                                    <span className="ast-debug-timing">耗时 {formatDuration(astDurationMs)}</span>
                                )}
                            </div>
                        </form>

                        {astError && <div className="ast-error">{astError}</div>}
                        {astResult && (
                            <AstViewer
                                payload={astResult}
                                expanded
                                rawVisible={astRawVisible}
                                onToggleRaw={() => setAstRawVisible((prev) => !prev)}
                                onOpenWorkFile={onOpenWorkFile}
                            />
                        )}
                    </div>
                ) : messages.length === 0 ? (
                    <div className="empty-debug">
                        <p>No messages.</p>
                    </div>
                ) : (
                    messages.map((msg) => {
                        const msgKey = `msg-${msg.id}`;
                        const usage = getTokenUsage(msg.raw_response);
                        const linkedCalls = callsByMessage.map.get(msg.id) || [];
                        return (
                            <div key={msg.id} className="debug-message">
                                <div className="debug-message-header" onClick={() => toggleExpandMessage(msgKey)}>
                                    <div className="message-title">
                                        <span className={`role-badge ${msg.role}`}>
                                            {msg.role === 'user' ? 'User' : msg.role === 'assistant' ? 'Assistant' : 'System'}
                                        </span>
                                        <span className="message-id">ID: {msg.id}</span>
                                        <span className="message-time">
                                            {new Date(msg.timestamp).toLocaleTimeString()}
                                        </span>
                                    </div>
                                    <button className="expand-btn">{expandedMessageId === msgKey ? '-' : '+'}</button>
                                </div>

                                {expandedMessageId === msgKey && (
                                    <div className="debug-message-body">
                                        <div className="debug-section">
                                            <h4>Content</h4>
                                            <div className="content-preview">{msg.content}</div>
                                        </div>

                                        {renderJsonSection('Raw Request', msg.raw_request)}
                                        {renderJsonSection('Raw Response', msg.raw_response)}

                                        {usage && (
                                            <div className="token-usage">
                                                <h5>Token Usage</h5>
                                                <div className="token-stats">
                                                    <span>Prompt: {usage.prompt}</span>
                                                    <span>Completion: {usage.completion}</span>
                                                    <span>Total: {usage.total}</span>
                                                </div>
                                            </div>
                                        )}

                                        <div className="debug-section">
                                            <div className="section-header">
                                                <h4>LLM Calls</h4>
                                            </div>
                                            {linkedCalls.length === 0 ? (
                                                <p className="no-debug-data">No LLM calls linked to this message.</p>
                                            ) : (
                                                linkedCalls.map(renderLLMCall)
                                            )}
                                        </div>

                                        {!msg.raw_request && !msg.raw_response && linkedCalls.length === 0 && (
                                            <div className="debug-section">
                                                <p className="no-debug-data">No debug data for this message.</p>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })
                )}

                {activeTab === 'llm' && callsByMessage.orphan.length > 0 && (
                    <>
                        <div className="debug-group-title">Unlinked LLM Calls</div>
                        {callsByMessage.orphan.map(renderLLMCall)}
                    </>
                )}
            </div>
        </div>
    );
}

export default DebugPanel;

