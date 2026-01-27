import { useState } from 'react';
import { Message } from '../types';
import './DebugPanel.css';

interface DebugPanelProps {
    messages: Message[];
    onClose: () => void;
}

function DebugPanel({ messages, onClose }: DebugPanelProps) {
    const [expandedId, setExpandedId] = useState<number | null>(null);

    const toggleExpand = (id: number) => {
        setExpandedId(expandedId === id ? null : id);
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        alert('已复制到剪贴板');
    };

    const formatJson = (obj: any) => {
        return JSON.stringify(obj, null, 2);
    };

    const getTokenUsage = (msg: Message) => {
        if (!msg.raw_response || !msg.raw_response.usage) return null;
        const usage = msg.raw_response.usage;
        return {
            prompt: usage.prompt_tokens || 0,
            completion: usage.completion_tokens || 0,
            total: usage.total_tokens || 0,
        };
    };

    return (
        <div className="debug-panel">
            <div className="debug-header">
                <h2>🐛 Debug 面板</h2>
                <button className="close-btn" onClick={onClose}>✕</button>
            </div>

            <div className="debug-content">
                {messages.length === 0 ? (
                    <div className="empty-debug">
                        <p>暂无消息</p>
                    </div>
                ) : (
                    messages.map((msg) => (
                        <div key={msg.id} className="debug-message">
                            <div
                                className="debug-message-header"
                                onClick={() => toggleExpand(msg.id)}
                            >
                                <div className="message-title">
                                    <span className={`role-badge ${msg.role}`}>
                                        {msg.role === 'user' ? '👤 用户' : '🤖 助手'}
                                    </span>
                                    <span className="message-id">ID: {msg.id}</span>
                                    <span className="message-time">
                                        {new Date(msg.timestamp).toLocaleTimeString('zh-CN')}
                                    </span>
                                </div>
                                <button className="expand-btn">
                                    {expandedId === msg.id ? '▼' : '▶'}
                                </button>
                            </div>

                            {expandedId === msg.id && (
                                <div className="debug-message-body">
                                    <div className="debug-section">
                                        <h4>📝 内容</h4>
                                        <div className="content-preview">
                                            {msg.content}
                                        </div>
                                    </div>

                                    {msg.raw_request && (
                                        <div className="debug-section">
                                            <div className="section-header">
                                                <h4>📤 原始请求</h4>
                                                <button
                                                    className="copy-btn"
                                                    onClick={() => copyToClipboard(formatJson(msg.raw_request))}
                                                >
                                                    📋 复制
                                                </button>
                                            </div>
                                            <pre className="json-viewer">
                                                {formatJson(msg.raw_request)}
                                            </pre>
                                        </div>
                                    )}

                                    {msg.raw_response && (
                                        <div className="debug-section">
                                            <div className="section-header">
                                                <h4>📥 原始响应</h4>
                                                <button
                                                    className="copy-btn"
                                                    onClick={() => copyToClipboard(formatJson(msg.raw_response))}
                                                >
                                                    📋 复制
                                                </button>
                                            </div>
                                            <pre className="json-viewer">
                                                {formatJson(msg.raw_response)}
                                            </pre>

                                            {getTokenUsage(msg) && (
                                                <div className="token-usage">
                                                    <h5>Token 使用统计</h5>
                                                    <div className="token-stats">
                                                        <span>Prompt: {getTokenUsage(msg)!.prompt}</span>
                                                        <span>Completion: {getTokenUsage(msg)!.completion}</span>
                                                        <span>Total: {getTokenUsage(msg)!.total}</span>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {!msg.raw_request && !msg.raw_response && (
                                        <div className="debug-section">
                                            <p className="no-debug-data">该消息无调试数据</p>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

export default DebugPanel;
