import { useState, useEffect } from 'react';
import { ChatSession } from '../types';
import { getSessions, deleteSession, updateSession } from '../api';
import ConfirmDialog from './ConfirmDialog';
import './SessionList.css';

interface SessionListProps {
    currentSessionId: string | null;
    onSelectSession: (sessionId: string) => void;
    onNewChat: () => void;
    onOpenConfig: () => void;
    onToggleDebug: () => void;
    debugActive: boolean;
    refreshTrigger?: number;
}

export default function SessionList({
    currentSessionId,
    onSelectSession,
    onNewChat,
    onOpenConfig,
    onToggleDebug,
    debugActive,
    refreshTrigger
}: SessionListProps) {
    const [sessions, setSessions] = useState<ChatSession[]>([]);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editTitle, setEditTitle] = useState('');
    const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);

    useEffect(() => {
        loadSessions();
    }, [refreshTrigger]);

    const loadSessions = async () => {
        try {
            const data = await getSessions();
            setSessions(data);
        } catch (error) {
            console.error('Failed to load sessions:', error);
        }
    };

    const handleDelete = (session: ChatSession, e: React.MouseEvent) => {
        e.stopPropagation();
        setDeleteTarget(session);
    };

    const handleConfirmDelete = async () => {
        if (!deleteTarget) return;
        try {
            await deleteSession(deleteTarget.id);
            await loadSessions();
            if (currentSessionId === deleteTarget.id) {
                onNewChat();
            }
        } catch (error) {
            console.error('Failed to delete session:', error);
            alert('删除会话失败');
        } finally {
            setDeleteTarget(null);
        }
    };

    const handleRename = async (id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingId(id);
        const session = sessions.find(s => s.id === id);
        setEditTitle(session?.title || '');
    };

    const handleSaveRename = async (id: string) => {
        if (!editTitle.trim()) return;

        try {
            await updateSession(id, { title: editTitle });
            await loadSessions();
            setEditingId(null);
        } catch (error) {
            console.error('Failed to rename session:', error);
            alert('重命名失败');
        }
    };

    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr);
        const now = new Date();
        if (Number.isNaN(date.getTime())) return dateStr;
        const diff = now.getTime() - date.getTime();
        if (diff < 60 * 1000) return '刚刚';
        const minutes = Math.floor(diff / (1000 * 60));
        if (minutes < 60) return `${minutes}分钟前`;
        const hours = Math.floor(diff / (1000 * 60 * 60));
        if (hours < 24) return `${hours}小时前`;
        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        if (days === 1) return '昨天';
        if (days < 7) return `${days}天前`;
        return date.toLocaleDateString('zh-CN');
    };

    return (
        <div className="session-list">
            <div className="session-list-header">
                <button className="header-btn new-chat-btn" onClick={onNewChat} title="新建对话" aria-label="新建对话">
                    <svg
                        className="header-icon"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                    >
                        <path d="M12 5v14" />
                        <path d="M5 12h14" />
                    </svg>
                </button>
                <div className="session-header-actions">
                    <button
                        className="header-btn"
                        onClick={onOpenConfig}
                        title="Manage configs"
                        aria-label="Manage configs"
                    >
                        <svg
                            className="header-icon"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                        >
                            <line x1="4" y1="6" x2="20" y2="6" />
                            <line x1="4" y1="12" x2="20" y2="12" />
                            <line x1="4" y1="18" x2="20" y2="18" />
                            <circle cx="9" cy="6" r="2" />
                            <circle cx="15" cy="12" r="2" />
                            <circle cx="11" cy="18" r="2" />
                        </svg>
                    </button>
                    <button
                        className={`header-btn ${debugActive ? 'active' : ''}`}
                        onClick={onToggleDebug}
                        title="Debug"
                        aria-label="Debug"
                    >
                        <svg
                            className="header-icon"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                        >
                            <rect x="9" y="9" width="6" height="8" rx="2" />
                            <path d="M8 9h8V6H8z" />
                            <path d="M4 13h4" />
                            <path d="M16 13h4" />
                            <path d="M6 7L4 5" />
                            <path d="M18 7l2-2" />
                        </svg>
                    </button>
                </div>
            </div>

            <div className="sessions-container">
                {sessions.length === 0 ? (
                    <p className="empty-sessions">暂无对话历史</p>
                ) : (
                    sessions.map((session) => (
                        <div
                            key={session.id}
                            className={`session-item ${currentSessionId === session.id ? 'active' : ''}`}
                            onClick={() => onSelectSession(session.id)}
                        >
                            {editingId === session.id ? (
                                <input
                                    type="text"
                                    className="session-rename-input"
                                    value={editTitle}
                                    onChange={(e) => setEditTitle(e.target.value)}
                                    onBlur={() => handleSaveRename(session.id)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') handleSaveRename(session.id);
                                        if (e.key === 'Escape') setEditingId(null);
                                    }}
                                    onClick={(e) => e.stopPropagation()}
                                    autoFocus
                                />
                            ) : (
                                <>
                                    <div className="session-info">
                                        <div className="session-title">{session.title}</div>
                                        <div className="session-meta">
                                            <span>{formatDate(session.updated_at || session.created_at)}</span>
                                            <span className="session-meta-sep">{'\u00b7'}</span>
                                            <span
                                                className={`session-work-path${session.work_path ? '' : ' empty'}`}
                                                title={session.work_path || '\u672a\u8bbe\u7f6e\u5de5\u4f5c\u8def\u5f84'}
                                            >
                                                {session.work_path || '\u672a\u8bbe\u7f6e\u5de5\u4f5c\u8def\u5f84'}
                                            </span>
                                        </div>
                                    </div>
                                    <div className="session-actions">
                                        <button
                                            className="session-action-btn"
                                            onClick={(e) => handleRename(session.id, e)}
                                            title="重命名"
                                        >
                                            <svg
                                                className="session-action-icon"
                                                viewBox="0 0 24 24"
                                                fill="none"
                                                stroke="currentColor"
                                                strokeWidth="1.8"
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                aria-hidden="true"
                                            >
                                                <path d="M12 20h9" />
                                                <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z" />
                                            </svg>
                                        </button>
                                        <button
                                            className="session-action-btn delete"
                                            onClick={(e) => handleDelete(session, e)}
                                            title="删除"
                                        >
                                            <svg
                                                className="session-action-icon"
                                                viewBox="0 0 24 24"
                                                fill="none"
                                                stroke="currentColor"
                                                strokeWidth="1.8"
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                aria-hidden="true"
                                            >
                                                <path d="M3 6h18" />
                                                <path d="M8 6V4h8v2" />
                                                <path d="M6 6l1 14h10l1-14" />
                                            </svg>
                                        </button>
                                    </div>
                                </>
                            )}
                        </div>
                    ))
                )}
            </div>
            <ConfirmDialog
                open={Boolean(deleteTarget)}
                title="删除会话"
                message={`确定要删除“${deleteTarget?.title || ''}”吗？此操作无法撤销。`}
                confirmLabel="删除"
                cancelLabel="取消"
                danger
                onCancel={() => setDeleteTarget(null)}
                onConfirm={handleConfirmDelete}
            />
        </div>
    );
}
