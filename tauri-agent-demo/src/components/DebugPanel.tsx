import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { Message, LLMCall } from '../types';
import './DebugPanel.css';

interface DebugPanelProps {
    messages: Message[];
    llmCalls: LLMCall[];
    onClose: () => void;
    focusTarget?: { key: string; messageId?: number; iteration?: number; callId?: number } | null;
}

function DebugPanel({ messages, llmCalls, onClose, focusTarget }: DebugPanelProps) {
    const [expandedMessageId, setExpandedMessageId] = useState<string | null>(null);
    const [expandedCallId, setExpandedCallId] = useState<string | null>(null);
    const [rawStreamVisible, setRawStreamVisible] = useState<Record<string, boolean>>({});
    const [panelWidth, setPanelWidth] = useState(400);
    const [focusedCallId, setFocusedCallId] = useState<number | null>(null);
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
                <h2>Debug</h2>
                <button className="close-btn" onClick={onClose}>
                    X
                </button>
            </div>

            <div className="debug-content">
                {messages.length === 0 ? (
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

                {callsByMessage.orphan.length > 0 && (
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
