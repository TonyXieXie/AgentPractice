import { useMemo, useState, useEffect, useRef } from 'react';
import { ChatSession } from '../types';
import { getSessions, deleteSession, updateSession, copySession } from '../api';
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
    inFlightBySession?: Record<string, boolean>;
    unreadBySession?: Record<string, boolean>;
    pendingPermissionBySession?: Record<string, boolean>;
}

export default function SessionList({
    currentSessionId,
    onSelectSession,
    onNewChat,
    onOpenConfig,
    onToggleDebug,
    debugActive,
    refreshTrigger,
    inFlightBySession,
    unreadBySession,
    pendingPermissionBySession
}: SessionListProps) {
    const [sessions, setSessions] = useState<ChatSession[]>([]);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editTitle, setEditTitle] = useState('');
    const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);
    const [contextMenu, setContextMenu] = useState<{
        session: ChatSession;
        x: number;
        y: number;
    } | null>(null);
    const contextMenuRef = useRef<HTMLDivElement | null>(null);
    const NO_WORK_PATH_KEY = '__no_work_path__';

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

    const handleCopyConversation = async (session: ChatSession) => {
        try {
            await copySession(session.id);
            await loadSessions();
        } catch (error) {
            console.error('Failed to copy session:', error);
            alert('拷贝对话失败');
        }
    };

    const startRename = (id: string) => {
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

    const openContextMenu = (session: ChatSession, event: React.MouseEvent) => {
        event.preventDefault();
        event.stopPropagation();
        const menuWidth = 176;
        const menuHeight = 88;
        const padding = 8;
        const maxX = window.innerWidth - menuWidth - padding;
        const maxY = window.innerHeight - menuHeight - padding;
        const x = Math.min(event.clientX, Math.max(padding, maxX));
        const y = Math.min(event.clientY, Math.max(padding, maxY));
        setContextMenu({ session, x, y });
    };

    useEffect(() => {
        if (!contextMenu) return;
        const handlePointer = (event: MouseEvent) => {
            if (contextMenuRef.current && contextMenuRef.current.contains(event.target as Node)) {
                return;
            }
            setContextMenu(null);
        };
        const handleKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') setContextMenu(null);
        };
        const handleScroll = () => setContextMenu(null);
        window.addEventListener('mousedown', handlePointer);
        window.addEventListener('keydown', handleKey);
        window.addEventListener('scroll', handleScroll, true);
        return () => {
            window.removeEventListener('mousedown', handlePointer);
            window.removeEventListener('keydown', handleKey);
            window.removeEventListener('scroll', handleScroll, true);
        };
    }, [contextMenu]);

    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr);
        const now = new Date();
        if (Number.isNaN(date.getTime())) return dateStr;
        const diff = now.getTime() - date.getTime();
        if (diff < 60 * 1000) return '0m';
        const minutes = Math.floor(diff / (1000 * 60));
        if (minutes < 60) return `${minutes}m`;
        const hours = Math.floor(diff / (1000 * 60 * 60));
        if (hours < 24) return `${hours}h`;
        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        if (days < 7) return `${days}d`;
        return date.toLocaleDateString('zh-CN');
    };

    const normalizeWorkPath = (path?: string | null) => {
        if (!path) return '';
        let normalized = path.trim();
        if (!normalized) return '';
        normalized = normalized.replace(/\\/g, '/');
        if (normalized.length > 1 && normalized.endsWith('/')) {
            normalized = normalized.slice(0, -1);
        }
        return normalized;
    };

    const getFolderLabel = (normalizedPath: string) => {
        if (!normalizedPath) return '未设置路径';
        const parts = normalizedPath.split('/').filter(Boolean);
        if (parts.length === 0) return normalizedPath;
        return parts[parts.length - 1];
    };

    const groupedSessions = useMemo(() => {
        const groups = new Map<
            string,
            { key: string; label: string; fullPath: string; sessions: ChatSession[] }
        >();

        for (const session of sessions) {
            const normalizedPath = normalizeWorkPath(session.work_path || null);
            if (!normalizedPath) {
                if (!groups.has(NO_WORK_PATH_KEY)) {
                    groups.set(NO_WORK_PATH_KEY, {
                        key: NO_WORK_PATH_KEY,
                        label: '未设置路径',
                        fullPath: '',
                        sessions: []
                    });
                }
                groups.get(NO_WORK_PATH_KEY)!.sessions.push(session);
                continue;
            }

            const key = normalizedPath;
            if (!groups.has(key)) {
                groups.set(key, {
                    key,
                    label: getFolderLabel(normalizedPath),
                    fullPath: normalizedPath,
                    sessions: []
                });
            }
            groups.get(key)!.sessions.push(session);
        }

        const result = Array.from(groups.values());
        const noPathIndex = result.findIndex((group) => group.key === NO_WORK_PATH_KEY);
        if (noPathIndex !== -1 && noPathIndex !== result.length - 1) {
            const [noPathGroup] = result.splice(noPathIndex, 1);
            result.push(noPathGroup);
        }
        return result;
    }, [sessions]);

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
                {groupedSessions.length === 0 ? (
                    <p className="empty-sessions">暂无对话历史</p>
                ) : (
                    groupedSessions.map((group) => (
                        <div key={group.key} className="session-group">
                            <div
                                className="session-group-header"
                                title={group.fullPath || '未设置路径'}
                            >
                                <svg
                                    className="session-group-icon"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                >
                                    <path d="M3 7.5h6l2 2H21v7.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.5Z" />
                                    <path d="M3 7.5a2 2 0 0 1 2-2h4l2 2" />
                                </svg>
                                <span className="session-group-title">{group.label}</span>
                            </div>
                            <div className="session-group-items">
                                {group.sessions.map((session) => {
                                    const isStreaming = Boolean(inFlightBySession?.[session.id]);
                                    const isPending = Boolean(pendingPermissionBySession?.[session.id]);
                                    const isUnread = !isStreaming && Boolean(unreadBySession?.[session.id]) && currentSessionId !== session.id;
                                    const showStatus = isPending || isStreaming || isUnread;
                                    const statusClass = isPending ? 'permission' : isStreaming ? 'streaming' : 'unread';

                                    return (
                                        <div
                                            key={session.id}
                                            className={`session-item ${currentSessionId === session.id ? 'active' : ''}`}
                                            onClick={() => onSelectSession(session.id)}
                                            onContextMenu={(e) => openContextMenu(session, e)}
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
                                                onContextMenu={(e) => e.stopPropagation()}
                                                autoFocus
                                            />
                                            ) : (
                                                <>
                                                    <div className="session-info">
                                                        <div className="session-row">
                                                            <span
                                                                className={`session-status ${showStatus ? statusClass : 'idle'}`}
                                                                aria-hidden="true"
                                                            >
                                                                {showStatus && <span className="session-status-indicator" />}
                                                            </span>
                                                            <div className="session-title">{session.title}</div>
                                                            <div className="session-right">
                                                                <span className="session-time">
                                                                    {formatDate(session.updated_at || session.created_at)}
                                                                </span>
                                                                <button
                                                                    className="session-action-btn delete session-delete-btn"
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
                                                        </div>
                                                    </div>
                                                </>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    ))
                )}
            </div>
            {contextMenu && (
                <div
                    ref={contextMenuRef}
                    className="session-context-menu"
                    style={{ left: contextMenu.x, top: contextMenu.y }}
                >
                    <button
                        type="button"
                        className="session-context-item"
                        onClick={() => {
                            startRename(contextMenu.session.id);
                            setContextMenu(null);
                        }}
                    >
                        重命名
                    </button>
                    <button
                        type="button"
                        className="session-context-item"
                        onClick={() => {
                            handleCopyConversation(contextMenu.session);
                            setContextMenu(null);
                        }}
                    >
                        拷贝对话
                    </button>
                </div>
            )}
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
