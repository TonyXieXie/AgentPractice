import { useState, useEffect } from 'react';
import { ChatSession } from '../types';
import { getSessions, deleteSession, updateSession } from '../api';
import ConfirmDialog from './ConfirmDialog';
import './SessionList.css';

interface SessionListProps {
    currentSessionId: string | null;
    onSelectSession: (sessionId: string) => void;
    onNewChat: () => void;
    refreshTrigger?: number;
}

export default function SessionList({
    currentSessionId,
    onSelectSession,
    onNewChat,
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
        const diff = now.getTime() - date.getTime();
        const days = Math.floor(diff / (1000 * 60 * 60 * 24));

        if (days === 0) return '今天';
        if (days === 1) return '昨天';
        if (days < 7) return `${days}天前`;
        return date.toLocaleDateString('zh-CN');
    };

    return (
        <div className="session-list">
            <div className="session-list-header">
                <h3>💬 对话历史</h3>
                <button className="new-chat-btn" onClick={onNewChat} title="新建对话">
                    ➕
                </button>
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
                                            {formatDate(session.created_at)} · {session.message_count || 0} 条消息
                                        </div>
                                    </div>
                                    <div className="session-actions">
                                        <button
                                            className="session-action-btn"
                                            onClick={(e) => handleRename(session.id, e)}
                                            title="重命名"
                                        >
                                            ✏️
                                        </button>
                                        <button
                                            className="session-action-btn delete"
                                            onClick={(e) => handleDelete(session, e)}
                                            title="删除"
                                        >
                                            🗑️
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
