import { useState, useEffect, useRef, useMemo } from 'react';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { openPath } from '@tauri-apps/plugin-opener';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { WebviewWindow } from '@tauri-apps/api/webviewWindow';
import './App.css';
import { Message, LLMConfig, LLMCall, ToolPermissionRequest, ReasoningEffort, AgentMode } from './types';
import {
  sendMessageAgentStream,
  getDefaultConfig,
  getConfig,
  getConfigs,
  getSession,
  getSessionMessages,
  getSessionLLMCalls,
  getSessionAgentSteps,
  stopAgentStream,
  rollbackSession,
  revertPatch,
  getToolPermissions,
  updateToolPermission,
  updateConfig,
  updateSession,
  AgentStep,
  AgentStepWithMessage,
} from './api';
import ConfigManager from './components/ConfigManager';
import SessionList from './components/SessionList';
import DebugPanel from './components/DebugPanel';
import AgentStepView from './components/AgentStepView';
import ConfirmDialog from './components/ConfirmDialog';

const DRAFT_SESSION_KEY = '__draft__';

const REASONING_OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: 'none', label: 'none' },
  { value: 'minimal', label: 'minimal' },
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
  { value: 'xhigh', label: 'xhigh' },
];

const AGENT_MODE_OPTIONS: { value: AgentMode; label: string; description: string }[] = [
  { value: 'default', label: '默认', description: '使用默认安全策略' },
  { value: 'shell_safe', label: 'Shell安全', description: '允许部分操作（基于允许列表）' },
  { value: 'super', label: '超级', description: '允许所有操作' },
];

const MAX_CONCURRENT_STREAMS = 10;
const WORK_PATH_MAX_LENGTH = 200;
const WORKDIR_BOUNDS_KEY = 'workdirWindowBounds';
const WORKDIR_DEFAULT_WIDTH = 1200;
const WORKDIR_DEFAULT_HEIGHT = 800;
const DEFAULT_MAX_CONTEXT_TOKENS = 200000;
const CONTEXT_RING_RADIUS = 10;

type WorkdirBounds = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
};

const getWorkdirWindowBounds = (): WorkdirBounds | null => {
  try {
    const raw = localStorage.getItem(WORKDIR_BOUNDS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkdirBounds> | null;
    if (!parsed || typeof parsed !== 'object') return null;
    const next: WorkdirBounds = {};
    if (Number.isFinite(parsed.width)) next.width = Math.max(640, Math.round(parsed.width as number));
    if (Number.isFinite(parsed.height)) next.height = Math.max(480, Math.round(parsed.height as number));
    if (Number.isFinite(parsed.x)) next.x = Math.round(parsed.x as number);
    if (Number.isFinite(parsed.y)) next.y = Math.round(parsed.y as number);
    return next;
  } catch {
    return null;
  }
};

const hashPath = (value: string) => {
  let hash = 5381;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) + hash + value.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
};

const makeWorkdirLabel = (path: string) => {
  const normalized = path.toLowerCase();
  const base = normalized
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 12);
  const safeBase = base || 'path';
  return `workdir-${safeBase}-${hashPath(normalized)}`;
};

const formatWorkPath = (path: string) => {
  if (!path) return '点击选择工作路径';
  if (path.length <= WORK_PATH_MAX_LENGTH) return path;
  const tailLength = Math.max(1, WORK_PATH_MAX_LENGTH - 3);
  return `...${path.slice(-tailLength)}`;
};

const estimateTokensForText = (text: string) => {
  if (!text) return 0;
  let ascii = 0;
  let nonAscii = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code <= 0x7f) {
      ascii += 1;
    } else {
      nonAscii += 1;
    }
  }
  return Math.ceil(ascii / 4) + nonAscii;
};

const collectTextFromContent = (content: any, bucket: string[]) => {
  if (!content) return;
  if (typeof content === 'string') {
    if (content.trim()) bucket.push(content);
    return;
  }
  if (Array.isArray(content)) {
    content.forEach((item) => collectTextFromContent(item, bucket));
    return;
  }
  if (typeof content === 'object') {
    if (typeof content.text === 'string') {
      if (content.text.trim()) bucket.push(content.text);
    }
    if (typeof content.content === 'string') {
      if (content.content.trim()) bucket.push(content.content);
    }
    if (Array.isArray(content.content)) {
      content.content.forEach((item: any) => collectTextFromContent(item, bucket));
    }
  }
};

const estimateTokensFromRequest = (request: Record<string, any> | null) => {
  if (!request) return 0;
  let total = 0;
  const texts: string[] = [];

  if (Array.isArray(request.messages)) {
    request.messages.forEach((msg: any) => {
      if (!msg) return;
      collectTextFromContent(msg.content, texts);
      total += 4;
    });
  }

  if (request.input) {
    if (typeof request.input === 'string') {
      texts.push(request.input);
    } else if (Array.isArray(request.input)) {
      request.input.forEach((item: any) => {
        if (item && Array.isArray(item.content)) {
          item.content.forEach((contentItem: any) => collectTextFromContent(contentItem, texts));
        } else {
          collectTextFromContent(item, texts);
        }
        total += 4;
      });
    } else {
      collectTextFromContent(request.input, texts);
    }
  }

  if (typeof request.instructions === 'string') {
    texts.push(request.instructions);
  }

  if (typeof request.prompt === 'string') {
    texts.push(request.prompt);
  }

  texts.forEach((text) => {
    total += estimateTokensForText(text);
  });

  return total;
};

const getLatestRequestPayload = (calls: LLMCall[], history: Message[]) => {
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const payload = calls[i]?.request_json;
    if (payload) return payload as Record<string, any>;
  }
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const payload = history[i]?.raw_request;
    if (payload) return payload as Record<string, any>;
  }
  return null;
};

type QueueItem = {
  id: string;
  message: string;
  sessionId: string | null;
  sessionKey: string;
  configId: string;
  agentMode: AgentMode;
  workPath?: string;
  enqueuedAt: number;
};

type InFlightState = {
  abortController: AbortController;
  stopRequested: boolean;
  activeAssistantId: number | null;
  tempAssistantId: number;
  sessionKey: string;
};

function App() {
  const [inputMsg, setInputMsg] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentConfig, setCurrentConfig] = useState<LLMConfig | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentWorkPath, setCurrentWorkPath] = useState('');
  const [showConfigManager, setShowConfigManager] = useState(false);
  const [sessionRefreshTrigger, setSessionRefreshTrigger] = useState(0);
  const [showSidebar] = useState(true);
  const [allConfigs, setAllConfigs] = useState<LLMConfig[]>([]);
  const [showConfigSelector, setShowConfigSelector] = useState(false);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const [llmCalls, setLlmCalls] = useState<LLMCall[]>([]);
  const [agentMode, setAgentMode] = useState<AgentMode>('default');
  const [showReasoningSelector, setShowReasoningSelector] = useState(false);
  const [showAgentModeSelector, setShowAgentModeSelector] = useState(false);
  const [queueTick, setQueueTick] = useState(0);
  const [inFlightTick, setInFlightTick] = useState(0);
  const [permissionTick, setPermissionTick] = useState(0);
  const [patchRevertBusy, setPatchRevertBusy] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<{ messageId: number; keepInput?: boolean } | null>(null);
  const [workPathMenu, setWorkPathMenu] = useState<{ x: number; y: number } | null>(null);
  const [isMaximized, setIsMaximized] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesCacheRef = useRef<Record<string, Message[]>>({});
  const workPathBySessionRef = useRef<Record<string, string>>({});
  const currentSessionIdRef = useRef<string | null>(null);
  const queueBySessionRef = useRef<Record<string, QueueItem[]>>({});
  const inFlightBySessionRef = useRef<Record<string, InFlightState>>({});
  const pendingPermissionBySessionRef = useRef<Record<string, ToolPermissionRequest | null>>({});
  const permissionBusyBySessionRef = useRef<Record<string, boolean>>({});
  const processingQueueRef = useRef(false);
  const pendingQueueRunRef = useRef(false);
  const appWindow = useMemo(() => getCurrentWindow(), []);

  useEffect(() => {
    loadDefaultConfig();
    loadAllConfigs();
  }, []);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    let cancelled = false;
    const syncMaximize = async () => {
      try {
        const next = await appWindow.isMaximized();
        if (!cancelled) {
          setIsMaximized(next);
        }
      } catch {
        // ignore
      }
    };
    syncMaximize();
    let unlisten: (() => void) | null = null;
    appWindow.onResized(() => {
      syncMaximize();
    }).then((stop) => {
      unlisten = stop;
    });
    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, [appWindow]);

  const handleTitlebarMinimize = async () => {
    try {
      await appWindow.minimize();
    } catch {
      // ignore
    }
  };

  const handleTitlebarMaximize = async () => {
    try {
      await appWindow.toggleMaximize();
      const next = await appWindow.isMaximized();
      setIsMaximized(next);
    } catch {
      // ignore
    }
  };

  const handleTitlebarClose = async () => {
    try {
      await appWindow.close();
    } catch {
      // ignore
    }
  };

  const handleTitlebarMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startDragging = (appWindow as unknown as { startDragging?: () => Promise<void> }).startDragging;
    if (typeof startDragging === 'function') {
      startDragging().catch(() => undefined);
    }
  };

  const handleTitlebarDoubleClick = () => {
    handleTitlebarMaximize();
  };

  useEffect(() => {
    if (!autoScrollRef.current) return;
    const container = messagesContainerRef.current;
    if (!container) return;
    const behavior: ScrollBehavior = container.scrollHeight > container.clientHeight ? 'smooth' : 'auto';
    const scrollToBottom = () => {
      if ('scrollTo' in container) {
        container.scrollTo({ top: container.scrollHeight, behavior });
      } else {
        container.scrollTop = container.scrollHeight;
      }
    };
    requestAnimationFrame(scrollToBottom);
  }, [messages]);

  useEffect(() => {
    if (!showConfigSelector && !showReasoningSelector && !showAgentModeSelector) return;
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (showConfigSelector && !target.closest('.model-selector-inline')) {
        setShowConfigSelector(false);
      }
      if (showReasoningSelector && !target.closest('.reasoning-selector-inline')) {
        setShowReasoningSelector(false);
      }
      if (showAgentModeSelector && !target.closest('.agent-mode-selector-inline')) {
        setShowAgentModeSelector(false);
      }
    };

    document.addEventListener('click', handleClickOutside);

    return () => {
      document.removeEventListener('click', handleClickOutside);
    };
  }, [showConfigSelector, showReasoningSelector, showAgentModeSelector]);

  useEffect(() => {
    if (!workPathMenu) return;
    const dismiss = () => setWorkPathMenu(null);
    window.addEventListener('click', dismiss);
    window.addEventListener('blur', dismiss);
    return () => {
      window.removeEventListener('click', dismiss);
      window.removeEventListener('blur', dismiss);
    };
  }, [workPathMenu]);

  useEffect(() => {
    if (showDebugPanel && currentSessionId) {
      refreshSessionDebug(currentSessionId);
    }
  }, [showDebugPanel, currentSessionId, sessionRefreshTrigger]);

  useEffect(() => {
    if (getInFlightCount() === 0) {
      if (Object.keys(pendingPermissionBySessionRef.current).length > 0) {
        pendingPermissionBySessionRef.current = {};
        bumpPermissions();
      }
      return;
    }
    let cancelled = false;
    let inFlight = false;

    const pollPermissions = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const pending = await getToolPermissions('pending');
        const nextBySession: Record<string, ToolPermissionRequest | null> = {};
        const inFlightKeys = Object.keys(inFlightBySessionRef.current);
        const fallbackKey = inFlightKeys.length === 1 ? inFlightKeys[0] : null;

        for (const item of pending) {
          if (item.tool_name !== 'run_shell') continue;
          const sessionKey = item.session_id ? getSessionKey(item.session_id) : fallbackKey;
          if (!sessionKey) continue;
          if (!nextBySession[sessionKey]) {
            nextBySession[sessionKey] = item;
          }
        }

        if (!cancelled) {
          pendingPermissionBySessionRef.current = nextBySession;
          bumpPermissions();
        }
      } catch (error) {
        if (!cancelled) {
          pendingPermissionBySessionRef.current = {};
          bumpPermissions();
        }
      } finally {
        inFlight = false;
      }
    };

    pollPermissions();
    const timer = window.setInterval(pollPermissions, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [inFlightTick]);

  const loadDefaultConfig = async () => {
    try {
      const config = await getDefaultConfig();
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load default config:', error);
      setShowConfigManager(true);
    }
  };

  const loadAllConfigs = async () => {
    try {
      const configs = await getConfigs();
      setAllConfigs(configs);
    } catch (error) {
      console.error('Failed to load configs:', error);
    }
  };

  const getSessionKey = (sessionId: string | null) => sessionId ?? DRAFT_SESSION_KEY;

  const getCurrentSessionKey = () => getSessionKey(currentSessionIdRef.current);

  const getSessionWorkPath = (sessionKey: string) => workPathBySessionRef.current[sessionKey] || '';

  const setSessionWorkPath = (sessionKey: string, nextPath: string) => {
    workPathBySessionRef.current[sessionKey] = nextPath;
    if (sessionKey === getCurrentSessionKey()) {
      setCurrentWorkPath(nextPath);
    }
  };

  const pickWorkPath = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: '\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'
      });
      if (!selected) return '';
      return Array.isArray(selected) ? (selected[0] || '') : selected;
    } catch (error) {
      console.error('Failed to pick work path:', error);
      return '';
    }
  };

  const applyWorkPath = async (sessionKey: string, sessionId: string | null, nextPath: string) => {
    if (!nextPath) return;
    setSessionWorkPath(sessionKey, nextPath);
    if (sessionId) {
      try {
        await updateSession(sessionId, { work_path: nextPath });
        setSessionRefreshTrigger((prev) => prev + 1);
      } catch (error) {
        console.error('Failed to update work path:', error);
      }
    }
  };

  const bumpQueue = () => setQueueTick((prev) => prev + 1);
  const bumpInFlight = () => setInFlightTick((prev) => prev + 1);
  const bumpPermissions = () => setPermissionTick((prev) => prev + 1);

  const getInFlightCount = () => Object.keys(inFlightBySessionRef.current).length;

  const getSessionQueue = (sessionKey: string) => queueBySessionRef.current[sessionKey] || [];

  const setSessionQueue = (sessionKey: string, next: QueueItem[]) => {
    if (next.length > 0) {
      queueBySessionRef.current[sessionKey] = next;
    } else {
      delete queueBySessionRef.current[sessionKey];
    }
    bumpQueue();
  };

  const enqueueSessionQueue = (sessionKey: string, item: QueueItem) => {
    const queue = getSessionQueue(sessionKey);
    setSessionQueue(sessionKey, [...queue, item]);
  };

  const removeSessionQueueItem = (sessionKey: string, itemId: string) => {
    const queue = getSessionQueue(sessionKey);
    const next = queue.filter((item) => item.id !== itemId);
    setSessionQueue(sessionKey, next);
  };

  const setSessionMessages = (sessionKey: string, next: Message[]) => {
    messagesCacheRef.current[sessionKey] = next;
    if (sessionKey === getCurrentSessionKey()) {
      setMessages(next);
    }
  };

  const updateSessionMessages = (sessionKey: string, updater: (prev: Message[]) => Message[]) => {
    const prev = messagesCacheRef.current[sessionKey] || [];
    const next = updater(prev);
    messagesCacheRef.current[sessionKey] = next;
    if (sessionKey === getCurrentSessionKey()) {
      setMessages(next);
    }
    return next;
  };

  const stashCurrentMessages = () => {
    const key = getCurrentSessionKey();
    messagesCacheRef.current[key] = messages;
  };

  const migrateSessionKey = (fromKey: string, toKey: string) => {
    if (!fromKey || !toKey || fromKey === toKey) return;

    const cached = messagesCacheRef.current[fromKey];
    if (cached) {
      if (!messagesCacheRef.current[toKey]) {
        messagesCacheRef.current[toKey] = cached;
      } else {
        messagesCacheRef.current[toKey] = [...messagesCacheRef.current[toKey], ...cached];
      }
      delete messagesCacheRef.current[fromKey];
    }

    const queued = queueBySessionRef.current[fromKey];
    if (queued && queued.length > 0) {
      const updatedQueue = queued.map((item) => ({
        ...item,
        sessionId: toKey,
        sessionKey: toKey,
      }));
      const existing = queueBySessionRef.current[toKey] || [];
      queueBySessionRef.current[toKey] = [...existing, ...updatedQueue];
      delete queueBySessionRef.current[fromKey];
      bumpQueue();
    }

    const inflight = inFlightBySessionRef.current[fromKey];
    if (inflight) {
      inFlightBySessionRef.current[toKey] = { ...inflight, sessionKey: toKey };
      delete inFlightBySessionRef.current[fromKey];
      bumpInFlight();
    }

    const pending = pendingPermissionBySessionRef.current[fromKey];
    if (pending) {
      pendingPermissionBySessionRef.current[toKey] = pending;
      delete pendingPermissionBySessionRef.current[fromKey];
      bumpPermissions();
    }

    if (fromKey in permissionBusyBySessionRef.current) {
      permissionBusyBySessionRef.current[toKey] = permissionBusyBySessionRef.current[fromKey];
      delete permissionBusyBySessionRef.current[fromKey];
      bumpPermissions();
    }

    if (fromKey in workPathBySessionRef.current) {
      workPathBySessionRef.current[toKey] = workPathBySessionRef.current[fromKey];
      delete workPathBySessionRef.current[fromKey];
      if (toKey === getCurrentSessionKey()) {
        setCurrentWorkPath(workPathBySessionRef.current[toKey] || '');
      }
    }
  };

  const runStreamForItem = async (
    item: QueueItem,
    startSessionKey: string,
    tempUserId: number,
    tempAssistantId: number,
    abortController: AbortController
  ) => {
    const userMessage = item.message;
    const targetSessionId = item.sessionId;
    let activeSessionKey = startSessionKey;
    let newSessionId: string | null = targetSessionId;
    let currentAssistantId = tempAssistantId;

    const tempUserMsg: Message = {
      id: tempUserId,
      session_id: targetSessionId || '',
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
    };

    const tempAssistantMsg: Message = {
      id: tempAssistantId,
      session_id: targetSessionId || '',
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      metadata: {
        agent_steps: [],
        agent_streaming: true,
        agent_answer_buffers: {},
        agent_thought_buffers: {},
        agent_action_buffers: {},
      }
    };

    updateSessionMessages(activeSessionKey, (prev) => [...prev, tempUserMsg, tempAssistantMsg]);

    try {
      const streamGenerator = sendMessageAgentStream(
        {
          message: userMessage,
          session_id: targetSessionId || undefined,
          config_id: item.configId,
          agent_mode: item.agentMode,
          work_path: item.workPath || undefined,
        },
        abortController.signal
      );

      for await (const chunk of streamGenerator) {
        const inflightState = inFlightBySessionRef.current[activeSessionKey];
        if (inflightState?.stopRequested && !('session_id' in chunk)) {
          continue;
        }

        if ('session_id' in chunk && typeof chunk.session_id === 'string') {
          newSessionId = chunk.session_id;
          const incomingUserId = (chunk as any).user_message_id;
          const incomingAssistantId = (chunk as any).assistant_message_id;
          if (typeof incomingAssistantId === 'number') {
            currentAssistantId = incomingAssistantId;
            const currentState = inFlightBySessionRef.current[activeSessionKey];
            if (currentState) {
              currentState.activeAssistantId = incomingAssistantId;
              if (currentState.stopRequested) {
                stopAgentStream(incomingAssistantId).catch(() => undefined);
              }
            }
          }
          if (incomingUserId || incomingAssistantId) {
            const resolvedSessionId = newSessionId || '';
            updateSessionMessages(activeSessionKey, (prev) =>
              prev.map((msg) => {
                if (typeof incomingUserId === 'number' && msg.id === tempUserId) {
                  return { ...msg, id: incomingUserId, session_id: resolvedSessionId };
                }
                if (typeof incomingAssistantId === 'number' && msg.id === tempAssistantId) {
                  return { ...msg, id: incomingAssistantId, session_id: resolvedSessionId };
                }
                if (!msg.session_id && newSessionId) {
                  return { ...msg, session_id: resolvedSessionId };
                }
                return msg;
              })
            );
          }
          if (!targetSessionId && newSessionId && currentSessionIdRef.current === null) {
            currentSessionIdRef.current = newSessionId;
            setCurrentSessionId(newSessionId);
            setSessionRefreshTrigger((prev) => prev + 1);
          }
          if (activeSessionKey === DRAFT_SESSION_KEY && newSessionId) {
            migrateSessionKey(activeSessionKey, newSessionId);
            activeSessionKey = newSessionId;
          } else if (newSessionId) {
            activeSessionKey = newSessionId;
          }
          continue;
        }

        if ('done' in chunk) {
          break;
        }

        const step = chunk as AgentStep;

        updateSessionMessages(activeSessionKey, (prev) =>
          prev.map((msg) => {
            if (msg.id !== currentAssistantId) return msg;

            const existingSteps = (msg.metadata?.agent_steps || []) as AgentStep[];
            let nextSteps = [...existingSteps];
            let nextMetadata = { ...(msg.metadata || {}) } as any;

            if (step.step_type === 'answer_delta') {
              const streamKey = String(step.metadata?.stream_key || 'answer_default');
              const buffers = { ...(nextMetadata.agent_answer_buffers || {}) } as Record<string, string>;
              const buffer = String(buffers[streamKey] || '') + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_answer_buffers = buffers;
              nextMetadata.agent_streaming = true;

              const streamingIndex = streamKey
                ? nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming && s.metadata?.stream_key === streamKey)
                : nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming);
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: buffer
                };
              } else {
                nextSteps.push({ step_type: 'answer', content: buffer, metadata: { streaming: true, stream_key: streamKey } });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'thought_delta') {
              const streamKey = String(step.metadata?.stream_key || 'assistant_content');
              const buffers = { ...(nextMetadata.agent_thought_buffers || {}) } as Record<string, string>;
              let baseBuffer = String(buffers[streamKey] || '');
              const fallbackAnswerIndex = nextSteps.findIndex(
                (s) => s.step_type === 'answer' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              if (!baseBuffer && fallbackAnswerIndex >= 0) {
                baseBuffer = String(nextSteps[fallbackAnswerIndex].content || '');
              }
              const buffer = baseBuffer + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_thought_buffers = buffers;
              nextMetadata.agent_streaming = true;
              if (fallbackAnswerIndex >= 0) {
                if (nextMetadata.agent_answer_buffers) {
                  nextMetadata.agent_answer_buffers = { ...(nextMetadata.agent_answer_buffers || {}), [streamKey]: '' };
                }
              }

              const streamingIndex = nextSteps.findIndex(
                (s) => s.step_type === 'thought' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              const fallbackIndex = streamingIndex >= 0 ? -1 : fallbackAnswerIndex;
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: buffer,
                  metadata: { ...(nextSteps[streamingIndex].metadata || {}), stream_key: streamKey, streaming: true }
                };
              } else if (fallbackIndex >= 0) {
                nextSteps[fallbackIndex] = {
                  step_type: 'thought',
                  content: buffer,
                  metadata: { ...(nextSteps[fallbackIndex].metadata || {}), stream_key: streamKey, streaming: true }
                };
              } else {
                nextSteps.push({ step_type: 'thought', content: buffer, metadata: { stream_key: streamKey, streaming: true } });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'action_delta') {
              const streamKey = String(step.metadata?.stream_key || 'tool-0');
              const toolName = String(step.metadata?.tool || '');
              const buffers = { ...(nextMetadata.agent_action_buffers || {}) } as Record<string, string>;
              const buffer = String(buffers[streamKey] || '') + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_action_buffers = buffers;
              nextMetadata.agent_streaming = true;

              const display = toolName ? `${toolName}[${buffer}]` : buffer;
              const streamingIndex = nextSteps.findIndex(
                (s) => s.step_type === 'action' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: display,
                  metadata: { ...(nextSteps[streamingIndex].metadata || {}), stream_key: streamKey, streaming: true, tool: toolName }
                };
              } else {
                nextSteps.push({
                  step_type: 'action',
                  content: display,
                  metadata: { stream_key: streamKey, streaming: true, tool: toolName }
                });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'answer') {
              nextMetadata.agent_streaming = false;
              if (step.metadata?.stream_key && nextMetadata.agent_answer_buffers) {
                nextMetadata.agent_answer_buffers = {
                  ...(nextMetadata.agent_answer_buffers || {}),
                  [String(step.metadata.stream_key)]: ''
                };
              }

              const streamKey = step.metadata?.stream_key;
              const streamingIndex = streamKey
                ? nextSteps.findIndex((s) => s.metadata?.streaming && s.metadata?.stream_key === streamKey)
                : nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming);
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = { ...step, metadata: { ...step.metadata } };
              } else {
                nextSteps.push(step);
              }
              nextMetadata.agent_steps = nextSteps;

              return { ...msg, metadata: nextMetadata, content: step.content };
            }

            if (step.step_type === 'error') {
              nextMetadata.agent_streaming = false;
              nextSteps.push(step);
              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata, content: step.content };
            }

            if (step.step_type === 'thought' || step.step_type === 'action') {
              const streamKey = step.metadata?.stream_key;
              if (streamKey) {
                const streamingIndex = nextSteps.findIndex(
                  (s) => s.metadata?.streaming && s.metadata?.stream_key === streamKey
                );
                if (streamingIndex >= 0) {
                  nextSteps[streamingIndex] = { ...step, metadata: { ...step.metadata } };
                } else {
                  nextSteps.push(step);
                }
              } else {
                nextSteps.push(step);
              }
            } else {
              nextSteps.push(step);
            }
            nextMetadata.agent_steps = nextSteps;
            nextMetadata.agent_streaming = true;
            return { ...msg, metadata: nextMetadata };
          })
        );
      }

      updateSessionMessages(activeSessionKey, (prev) =>
        prev.map((msg) =>
          msg.id === currentAssistantId
            ? { ...msg, metadata: { ...(msg.metadata || {}), agent_streaming: false, agent_answer_buffers: {} } }
            : msg
        )
      );

      if (newSessionId) {
        setSessionRefreshTrigger((prev) => prev + 1);
        await refreshSessionDebug(newSessionId);
      }
    } catch (error: any) {
      const stopped = inFlightBySessionRef.current[activeSessionKey]?.stopRequested;
      if (error?.name === 'AbortError' || stopped) {
        // User stopped streaming
      } else {
        console.error('Failed to send message:', error);
        const errorMsg: Message = {
          id: Date.now() + 2,
          session_id: newSessionId || targetSessionId || '',
          role: 'assistant',
          content: `Chat error: ${error.message || 'Please check whether the backend is running.'}`,
          timestamp: new Date().toISOString(),
        };
        updateSessionMessages(activeSessionKey, (prev) => [...prev.filter((m) => m.id !== currentAssistantId), errorMsg]);
      }
    } finally {
      delete inFlightBySessionRef.current[activeSessionKey];
      bumpInFlight();
      processQueues();
    }
  };

  const startStreamForItem = (item: QueueItem, sessionKey: string) => {
    if (inFlightBySessionRef.current[sessionKey]) return;
    const tempBase = Date.now() + Math.floor(Math.random() * 1000);
    const tempUserId = -tempBase;
    const tempAssistantId = -(tempBase + 1);
    const abortController = new AbortController();
    inFlightBySessionRef.current[sessionKey] = {
      abortController,
      stopRequested: false,
      activeAssistantId: null,
      tempAssistantId,
      sessionKey,
    };
    bumpInFlight();
    void runStreamForItem(item, sessionKey, tempUserId, tempAssistantId, abortController);
  };

  const processQueues = () => {
    if (processingQueueRef.current) {
      pendingQueueRunRef.current = true;
      return;
    }
    processingQueueRef.current = true;
    try {
      let available = MAX_CONCURRENT_STREAMS - getInFlightCount();
      if (available <= 0) return;

      const candidates = Object.entries(queueBySessionRef.current)
        .filter(([sessionKey, queue]) => queue.length > 0 && !inFlightBySessionRef.current[sessionKey])
        .map(([sessionKey, queue]) => ({ sessionKey, item: queue[0] }))
        .sort((a, b) => a.item.enqueuedAt - b.item.enqueuedAt);

      for (const candidate of candidates) {
        if (available <= 0) break;
        const queue = queueBySessionRef.current[candidate.sessionKey] || [];
        if (!queue.length || queue[0].id !== candidate.item.id) continue;
        const nextQueue = queue.slice(1);
        if (nextQueue.length > 0) {
          queueBySessionRef.current[candidate.sessionKey] = nextQueue;
        } else {
          delete queueBySessionRef.current[candidate.sessionKey];
        }
        bumpQueue();
        startStreamForItem(candidate.item, candidate.sessionKey);
        available -= 1;
      }
    } finally {
      processingQueueRef.current = false;
      if (pendingQueueRunRef.current) {
        pendingQueueRunRef.current = false;
        processQueues();
      }
    }
  };

  const refreshSessionDebug = async (sessionId: string) => {
    try {
      const calls = await getSessionLLMCalls(sessionId);
      setLlmCalls(calls);
    } catch (error) {
      console.error('Failed to load LLM calls:', error);
    }
  };

  const handleSwitchConfig = async (configId: string) => {
    try {
      const config = await getConfig(configId);
      setCurrentConfig(config);
      setShowConfigSelector(false);
    } catch (error) {
      console.error('Failed to switch config:', error);
      alert('Failed to switch config.');
    }
  };

  const handleReasoningChange = async (value: ReasoningEffort) => {
    if (!currentConfig) return;
    try {
      const updated = await updateConfig(currentConfig.id, { reasoning_effort: value });
      setCurrentConfig(updated);
      setAllConfigs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setShowReasoningSelector(false);
    } catch (error) {
      console.error('Failed to update reasoning:', error);
      alert('Failed to update reasoning.');
    }
  };

  const handleWorkPathPick = async () => {
    const selected = await pickWorkPath();
    if (!selected) return;
    const sessionKey = getCurrentSessionKey();
    await applyWorkPath(sessionKey, currentSessionIdRef.current, selected);
  };

  const openWorkDirWindow = async (path: string) => {
    const label = makeWorkdirLabel(path);
    const existing = await WebviewWindow.getByLabel(label);
    if (existing) {
      try {
        await existing.show();
        await existing.setFocus();
      } catch {
        // ignore focus errors
      }
      void existing.emit('workdir:ping', { target: label });
      void existing.emit('workdir:set', { path, target: label });
      return;
    }

    const bounds = getWorkdirWindowBounds();
    const url = `/?window=workdir&path=${encodeURIComponent(path)}`;
    const win = new WebviewWindow(label, {
      title: '工作目录',
      url,
      width: bounds?.width ?? WORKDIR_DEFAULT_WIDTH,
      height: bounds?.height ?? WORKDIR_DEFAULT_HEIGHT,
      x: bounds?.x,
      y: bounds?.y,
    });

    win.once('tauri://created', () => {
      void win.emit('workdir:set', { path, target: label });
    });

    win.once('tauri://error', (event) => {
      console.error('Failed to create workdir window:', event);
    });
  };

  const handleWorkPathClick = async () => {
    if (!currentWorkPath) {
      await handleWorkPathPick();
      return;
    }
    await openWorkDirWindow(currentWorkPath);
  };

  const handleWorkPathContextMenu = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setWorkPathMenu({ x: event.clientX, y: event.clientY });
  };

  const enqueueMessage = async (message: string, sessionId: string | null) => {
    if (!message.trim()) return;
    if (!currentConfig) {
      alert('Please configure an LLM first.');
      return;
    }
    autoScrollRef.current = true;

    const sessionKey = getSessionKey(sessionId);
    let workPath = getSessionWorkPath(sessionKey);
    if (!sessionId && !workPath) {
      const selected = await pickWorkPath();
      if (selected) {
        await applyWorkPath(sessionKey, null, selected);
        workPath = selected;
      }
    }

    const queueItem: QueueItem = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      message,
      sessionId,
      sessionKey,
      configId: currentConfig.id,
      agentMode,
      workPath,
      enqueuedAt: Date.now(),
    };

    enqueueSessionQueue(sessionKey, queueItem);
    processQueues();
  };

  const handleSend = async () => {
    const userMessage = inputMsg.trim();
    if (!userMessage) return;
    setInputMsg('');
    await enqueueMessage(userMessage, currentSessionIdRef.current);
  };

  const handleRetryMessage = async (messageId: number | null, message: string) => {
    if (!message) return;
    if (isStreamingCurrent) {
      alert('请先停止当前输出再重试。');
      return;
    }
    let rollbackOk = true;
    if (messageId && currentSessionIdRef.current) {
      rollbackOk = await rollbackToMessage(messageId, { keepInput: false });
    }
    if (!rollbackOk) return;
    await enqueueMessage(message, currentSessionIdRef.current);
  };

  const handleRemoveQueuedItem = (itemId: string) => {
    const sessionKey = getCurrentSessionKey();
    removeSessionQueueItem(sessionKey, itemId);
    processQueues();
  };

  const applyStopNoteToMessage = (msg: Message) => {
    const note = '\n\n[用户主动停止输出]';
    const nextMetadata = { ...(msg.metadata || {}) } as any;
    const steps = (nextMetadata.agent_steps || []) as AgentStep[];
    if (steps.length > 0) {
      const lastIndex = [...steps].reverse().findIndex(
        (step) => step.step_type === 'answer' || step.step_type === 'answer_delta'
      );
      const idx = lastIndex >= 0 ? steps.length - 1 - lastIndex : -1;
      if (idx >= 0) {
        const target = steps[idx];
        steps[idx] = {
          ...target,
          content: `${target.content || ''}${note}`,
          metadata: { ...(target.metadata || {}), stopped_by_user: true }
        };
      } else {
        steps.push({ step_type: 'answer', content: note, metadata: { stopped_by_user: true } });
      }
      nextMetadata.agent_steps = steps;
      nextMetadata.agent_streaming = false;
      nextMetadata.agent_answer_buffers = {};
      nextMetadata.agent_thought_buffers = {};
      nextMetadata.agent_action_buffers = {};
      return {
        ...msg,
        content: `${msg.content || ''}${note}`,
        metadata: nextMetadata
      };
    }

    return {
      ...msg,
      content: `${msg.content || ''}${note}`,
      metadata: { ...nextMetadata, agent_streaming: false }
    };
  };

  const handleStop = async () => {
    const sessionKey = getCurrentSessionKey();
    const inflight = inFlightBySessionRef.current[sessionKey];
    if (!inflight) return;
    inflight.stopRequested = true;
    const activeAssistantId = inflight.activeAssistantId;
    let assistantId: number | null = activeAssistantId ?? inflight.tempAssistantId;
    if (activeAssistantId) {
      try {
        await stopAgentStream(activeAssistantId);
      } catch (error) {
        console.error('Failed to stop stream:', error);
      }
    }
    updateSessionMessages(sessionKey, (prev) => {
      if (!assistantId) {
        const lastAssistant = [...prev].reverse().find((msg) => msg.role === 'assistant');
        assistantId = lastAssistant?.id ?? null;
      }
      if (!assistantId) return prev;
      return prev.map((msg) => (msg.id === assistantId ? applyStopNoteToMessage(msg) : msg));
    });
  };

  const handlePermissionDecision = async (status: 'approved' | 'denied') => {
    const sessionKey = getCurrentSessionKey();
    const pending = pendingPermissionBySessionRef.current[sessionKey];
    if (!pending || permissionBusyBySessionRef.current[sessionKey]) return;
    permissionBusyBySessionRef.current[sessionKey] = true;
    bumpPermissions();
    try {
      await updateToolPermission(pending.id, status);
      if (pendingPermissionBySessionRef.current[sessionKey]?.id === pending.id) {
        delete pendingPermissionBySessionRef.current[sessionKey];
      }
    } catch (error) {
      console.error('Failed to update permission:', error);
      alert('权限更新失败');
    } finally {
      permissionBusyBySessionRef.current[sessionKey] = false;
      bumpPermissions();
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    try {
      autoScrollRef.current = true;
      stashCurrentMessages();
      currentSessionIdRef.current = sessionId;
      setCurrentSessionId(sessionId);
      setShowConfigSelector(false);
      setShowReasoningSelector(false);

      const sessionKey = getSessionKey(sessionId);
      const cached = messagesCacheRef.current[sessionKey];
      const isStreamingSession = Boolean(inFlightBySessionRef.current[sessionKey]);
      const hasStreaming = Boolean(cached?.some((msg) => msg.metadata?.agent_streaming));
      if (cached && (isStreamingSession || hasStreaming)) {
        setSessionMessages(sessionKey, cached);
      }

      const [session, calls] = await Promise.all([
        getSession(sessionId),
        getSessionLLMCalls(sessionId),
      ]);

      setLlmCalls(calls);
      const sessionWorkPath = session.work_path || '';
      workPathBySessionRef.current[session.id] = sessionWorkPath;
      setCurrentWorkPath(sessionWorkPath);

      if (!cached || (!isStreamingSession && !hasStreaming)) {
        const [msgs, steps] = await Promise.all([
          getSessionMessages(sessionId),
          getSessionAgentSteps(sessionId),
        ]);

        const stepMap = new Map<number, AgentStep[]>();
        (steps as AgentStepWithMessage[]).forEach((step) => {
          const list = stepMap.get(step.message_id) || [];
          list.push({ step_type: step.step_type as AgentStep['step_type'], content: step.content, metadata: step.metadata });
          stepMap.set(step.message_id, list);
        });

        const hydratedMessages = msgs.map((msg) => {
          const agentSteps = stepMap.get(msg.id) || [];
          if (!agentSteps.length) return msg;
          return {
            ...msg,
            metadata: { ...(msg.metadata || {}), agent_steps: agentSteps, agent_streaming: false }
          };
        });

        setSessionMessages(sessionKey, hydratedMessages);
      }

      const config = await getConfig(session.config_id);
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load session:', error);
      alert('Failed to load session.');
    }
  };

  const handleNewChat = async () => {
    const sourceKey = getCurrentSessionKey();
    const sourcePath = getSessionWorkPath(sourceKey);

    stashCurrentMessages();
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    setSessionMessages(DRAFT_SESSION_KEY, []);
    setLlmCalls([]);
    autoScrollRef.current = true;

    if (sourcePath) {
      setSessionWorkPath(DRAFT_SESSION_KEY, sourcePath);
      return;
    }

    setSessionWorkPath(DRAFT_SESSION_KEY, '');
    const selected = await pickWorkPath();
    if (selected) {
      await applyWorkPath(DRAFT_SESSION_KEY, null, selected);
    }
  };

  const handleRollback = (messageId: number) => {
    setRollbackTarget({ messageId, keepInput: true });
  };

  const rollbackToMessage = async (
    messageId: number,
    options?: { keepInput?: boolean }
  ) => {
    if (!currentSessionId) return false;
    const currentKey = getCurrentSessionKey();
    if (inFlightBySessionRef.current[currentKey]) {
      alert('请先停止当前输出再回撤。');
      return false;
    }

    try {
      const result = await rollbackSession(currentSessionId, messageId);
      await handleSelectSession(currentSessionId);
      if (options?.keepInput) {
        setInputMsg(result.input_message || '');
        inputRef.current?.focus();
      } else {
        setInputMsg('');
      }
      setSessionRefreshTrigger((prev) => prev + 1);
      await refreshSessionDebug(currentSessionId);
      return true;
    } catch (error) {
      console.error('Failed to rollback session:', error);
      alert('回撤失败');
      return false;
    }
  };

  const handleConfirmRollback = async () => {
    if (!rollbackTarget) return;
    const { messageId, keepInput } = rollbackTarget;
    setRollbackTarget(null);
    await rollbackToMessage(messageId, { keepInput });
  };

  const handleRevertPatch = async (revertPatchContent: string) => {
    if (!currentSessionId) {
      alert('请先选择会话。');
      return;
    }
    if (patchRevertBusy) return;
    const currentKey = getCurrentSessionKey();
    if (inFlightBySessionRef.current[currentKey]) {
      alert('请先停止当前输出再撤销。');
      return;
    }
    setPatchRevertBusy(true);
    try {
      await revertPatch(currentSessionId, revertPatchContent);
      await handleSelectSession(currentSessionId);
      setSessionRefreshTrigger((prev) => prev + 1);
      await refreshSessionDebug(currentSessionId);
    } catch (error) {
      console.error('Failed to revert patch:', error);
      alert('撤销失败');
    } finally {
      setPatchRevertBusy(false);
    }
  };

  const handleMessagesScroll = () => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const threshold = 10;
    const currentScrollTop = container.scrollTop;
    const distanceToBottom = container.scrollHeight - currentScrollTop - container.clientHeight;
    const nearBottom = distanceToBottom <= threshold;
    if (currentScrollTop < lastScrollTopRef.current) {
      autoScrollRef.current = false;
    } else if (nearBottom) {
      autoScrollRef.current = true;
    }
    lastScrollTopRef.current = currentScrollTop;
  };

  const latestAssistantId =
    [...messages].reverse().find((msg) => msg.role === 'assistant')?.id ?? null;
  const currentSessionKey = getSessionKey(currentSessionId);
  const isStreamingCurrent = useMemo(
    () => Boolean(inFlightBySessionRef.current[currentSessionKey]),
    [currentSessionKey, inFlightTick]
  );
  const currentSessionQueue = useMemo(
    () => getSessionQueue(currentSessionKey),
    [currentSessionKey, queueTick]
  );
  const currentPendingPermission = useMemo(
    () => pendingPermissionBySessionRef.current[currentSessionKey] || null,
    [currentSessionKey, permissionTick]
  );
  const currentPermissionBusy = useMemo(
    () => Boolean(permissionBusyBySessionRef.current[currentSessionKey]),
    [currentSessionKey, permissionTick]
  );
  const currentReasoning = (currentConfig?.reasoning_effort || 'medium') as ReasoningEffort;
  const workPathDisplay = useMemo(() => formatWorkPath(currentWorkPath), [currentWorkPath]);
  const contextUsage = useMemo(() => {
    const maxTokens = currentConfig?.max_context_tokens || DEFAULT_MAX_CONTEXT_TOKENS;
    const lastRequest = getLatestRequestPayload(llmCalls, messages);
    const usedTokens = estimateTokensFromRequest(lastRequest);
    const ratio = maxTokens > 0 ? Math.min(1, usedTokens / maxTokens) : 0;
    return { usedTokens, maxTokens, ratio };
  }, [currentConfig?.max_context_tokens, llmCalls, messages]);
  const contextRing = useMemo(() => {
    const circumference = 2 * Math.PI * CONTEXT_RING_RADIUS;
    const dashOffset = circumference * (1 - contextUsage.ratio);
    return { circumference, dashOffset };
  }, [contextUsage.ratio]);

  return (
    <div className="app-shell">
      <div className="app-titlebar">
        <div
          className="titlebar-left"
          data-tauri-drag-region
          onMouseDown={handleTitlebarMouseDown}
          onDoubleClick={handleTitlebarDoubleClick}
        >
          <div className="titlebar-appname">GYY</div>
          <div className="titlebar-divider" />
          <div className="titlebar-subtitle">Agent Chat</div>
        </div>
        <div className="titlebar-actions" data-tauri-drag-region="false">
          <button
            type="button"
            className="titlebar-btn"
            onClick={handleTitlebarMinimize}
            aria-label="Minimize"
            title="Minimize"
            data-tauri-drag-region="false"
          >
            <svg viewBox="0 0 12 12" aria-hidden="true" data-tauri-drag-region="false">
              <rect x="2" y="6" width="8" height="1.2" rx="0.6" fill="currentColor" />
            </svg>
          </button>
          <button
            type="button"
            className="titlebar-btn"
            onClick={handleTitlebarMaximize}
            aria-label={isMaximized ? 'Restore' : 'Maximize'}
            title={isMaximized ? 'Restore' : 'Maximize'}
            data-tauri-drag-region="false"
          >
            {isMaximized ? (
              <svg viewBox="0 0 12 12" aria-hidden="true" data-tauri-drag-region="false">
                <path
                  d="M4 3h5a1 1 0 0 1 1 1v5M3 4a1 1 0 0 1 1-1h4v1H4v4H3z"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1"
                />
                <rect x="3" y="4" width="5" height="5" fill="none" stroke="currentColor" strokeWidth="1" />
              </svg>
            ) : (
              <svg viewBox="0 0 12 12" aria-hidden="true" data-tauri-drag-region="false">
                <rect x="3" y="3" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1" />
              </svg>
            )}
          </button>
          <button
            type="button"
            className="titlebar-btn close"
            onClick={handleTitlebarClose}
            aria-label="Close"
            title="Close"
            data-tauri-drag-region="false"
          >
            <svg viewBox="0 0 12 12" aria-hidden="true" data-tauri-drag-region="false">
              <path
                d="M3.2 3.2l5.6 5.6M8.8 3.2l-5.6 5.6"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </div>
      <div className="app-container">
      {showSidebar && (
        <SessionList
          currentSessionId={currentSessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
          onOpenConfig={() => setShowConfigManager(true)}
          onToggleDebug={() => setShowDebugPanel((prev) => !prev)}
          debugActive={showDebugPanel}
          refreshTrigger={sessionRefreshTrigger}
        />
      )}

      <div className="main-content">
        <div className="chat-container">
          <div className="messages" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>Welcome to Agent Chat</h2>
                <p>Type a message to get started.</p>
                {!currentConfig && <p className="warning">Please configure an LLM.</p>}
              </div>
            ) : (
              messages.map((msg, index) => {
                const steps = (msg.metadata?.agent_steps || []) as AgentStep[];
                const streaming = Boolean(msg.metadata?.agent_streaming);
                const showPermission = Boolean(currentPendingPermission && msg.id === latestAssistantId);
                const previousUser = (() => {
                  for (let i = index - 1; i >= 0; i -= 1) {
                    if (messages[i].role === 'user') return messages[i];
                  }
                  return null;
                })();

                return (
                  <div key={msg.id} className={`message ${msg.role}`}>
                    <div className="message-content">
                      {msg.role === 'assistant' && (steps.length > 0 || showPermission) ? (
                        <AgentStepView
                          steps={steps}
                          streaming={streaming}
                          pendingPermission={showPermission ? currentPendingPermission : null}
                          onPermissionDecision={handlePermissionDecision}
                          permissionBusy={currentPermissionBusy}
                          onRollbackMessage={
                            previousUser?.id ? () => handleRollback(previousUser.id) : undefined
                          }
                          onRetryMessage={
                            previousUser?.content
                              ? () => handleRetryMessage(previousUser.id, previousUser.content)
                              : undefined
                          }
                          onRevertPatch={handleRevertPatch}
                          patchRevertBusy={patchRevertBusy}
                        />
                      ) : msg.role === 'user' ? (
                        <>
                          <div className="message-text">{msg.content}</div>
                          <button
                            className="message-action-btn icon inline"
                            onClick={() => handleRollback(msg.id)}
                            title={'\u56de\u64a4\u5230\u6b64\u6d88\u606f'}
                            aria-label={'\u56de\u64a4\u5230\u6b64\u6d88\u606f'}
                          >
                            <svg className="icon-undo" viewBox="0 0 24 24" aria-hidden="true">
                              <path
                                d="M7 8L3 12l4 4M3 12h11a5 5 0 0 1 0 10h-4"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              />
                            </svg>
                          </button>
                        </>
                      ) : (
                        msg.content
                      )}
                    </div>
                    <div className="message-time">{new Date(msg.timestamp).toLocaleTimeString()}</div>
                  </div>
                );
              })
            )}
            {isStreamingCurrent && (
              <div className="message assistant loading">
                <div className="message-content">
                  <span className="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                  </span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="input-area">
            <input
              onChange={(e) => {
                setInputMsg(e.currentTarget.value);
                autoScrollRef.current = true;
              }}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              value={inputMsg}
              placeholder={currentConfig ? 'Type a message...' : 'Please configure an LLM'}
              disabled={!currentConfig}
              ref={inputRef}
            />

            {currentSessionQueue.length > 0 && (
              <div className="queue-panel">
                <div className="queue-header">
                  <span>{'\u6392\u961f\u6d88\u606f'}</span>
                  <span className="queue-count">{currentSessionQueue.length}</span>
                </div>
                <div className="queue-list">
                  {currentSessionQueue.map((item) => (
                    <div key={item.id} className="queue-item">
                      <span className="queue-text">{item.message}</span>
                      <button
                        type="button"
                        className="queue-remove"
                        onClick={() => handleRemoveQueuedItem(item.id)}
                        aria-label={'\u5220\u9664\u6392\u961f\u6d88\u606f'}
                        title={'\u5220\u9664\u6392\u961f\u6d88\u606f'}
                      >
                        {'\u5220\u9664'}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="input-footer">
              {currentConfig && (
                <div className="input-controls">
                  <div className="model-selector-inline">
                    <button
                      className={`model-selector-btn ${showConfigSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowConfigSelector(!showConfigSelector);
                      }}
                      aria-label={`Select model: ${currentConfig.name}`}
                      title={`Model: ${currentConfig.name}`}
                    >
                      <span className="selector-text">{currentConfig.name}</span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showConfigSelector && (
                      <div className="config-dropdown-inline">
                        {allConfigs.map((config) => (
                          <div
                            key={config.id}
                            className={`config-option ${config.id === currentConfig.id ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSwitchConfig(config.id);
                              setShowConfigSelector(false);
                            }}
                          >
                            <div className="config-name">{config.name}</div>
                            <div className="config-meta">
                              {config.api_format} / {config.api_profile} / {config.model}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="agent-mode-selector-inline">
                    <button
                      type="button"
                      className={`agent-mode-selector-btn ${showAgentModeSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowAgentModeSelector(!showAgentModeSelector);
                      }}
                      disabled={!currentConfig}
                      aria-label={`Agent mode: ${agentMode}`}
                      title={`Agent\u6a21\u5f0f: ${agentMode}`}
                    >
                      <span className="selector-text">
                        {AGENT_MODE_OPTIONS.find((opt) => opt.value === agentMode)?.label || agentMode}
                      </span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showAgentModeSelector && (
                      <div className="agent-mode-dropdown-inline">
                        {AGENT_MODE_OPTIONS.map((option) => (
                          <div
                            key={option.value}
                            className={`agent-mode-option ${option.value === agentMode ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setAgentMode(option.value);
                              setShowAgentModeSelector(false);
                            }}
                            title={option.description}
                          >
                            <div className="agent-mode-label">{option.label}</div>
                            <div className="agent-mode-desc">{option.description}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="reasoning-selector-inline">
                    <button
                      className={`reasoning-selector-btn ${showReasoningSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowReasoningSelector(!showReasoningSelector);
                      }}
                      disabled={!currentConfig}
                      aria-label={`Reasoning: ${currentReasoning}`}
                      title={`Reasoning: ${currentReasoning}`}
                    >
                      <span className="selector-text">{currentReasoning}</span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showReasoningSelector && (
                      <div className="reasoning-dropdown-inline">
                        {REASONING_OPTIONS.map((option) => (
                          <div
                            key={option.value}
                            className={`reasoning-option ${option.value === currentReasoning ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleReasoningChange(option.value);
                            }}
                          >
                            {option.label}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                    <button
                      type="button"
                      className="work-path-row"
                      onClick={handleWorkPathClick}
                      onContextMenu={handleWorkPathContextMenu}
                      title={currentWorkPath || '\u70b9\u51fb\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'}
                      aria-label={'\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'}
                    >
                    <span className={`work-path-value${currentWorkPath ? '' : ' empty'}`}>
                      {workPathDisplay}
                    </span>
                  </button>
                  {workPathMenu && (
                    <div
                      className="work-path-menu"
                      style={{
                        top: Math.min(workPathMenu.y, window.innerHeight - 90),
                        left: Math.min(workPathMenu.x, window.innerWidth - 220)
                      }}
                      onClick={(event) => event.stopPropagation()}
                      onContextMenu={(event) => event.preventDefault()}
                    >
                      <button
                        type="button"
                        className="work-path-menu-item"
                        onClick={async () => {
                          setWorkPathMenu(null);
                          await handleWorkPathPick();
                        }}
                      >
                        重新选择工作路径
                      </button>
                      <button
                        type="button"
                        className="work-path-menu-item"
                        disabled={!currentWorkPath}
                        onClick={async () => {
                          if (!currentWorkPath) return;
                          setWorkPathMenu(null);
                          try {
                            await openPath(currentWorkPath);
                          } catch {
                            // ignore open errors
                          }
                        }}
                      >
                        在资源管理器打开
                      </button>
                    </div>
                  )}
                </div>
              )}

              <div className="input-actions">
                {currentConfig && (
                  <div
                    className={`context-usage${contextUsage.ratio >= 0.8 ? ' warn' : contextUsage.ratio >= 0.6 ? ' mid' : ''}`}
                    title={`Context ${contextUsage.usedTokens} / ${contextUsage.maxTokens} tokens`}
                    aria-label={`Context usage ${contextUsage.usedTokens} of ${contextUsage.maxTokens} tokens`}
                  >
                    <svg viewBox="0 0 36 36" aria-hidden="true">
                      <circle
                        className="context-ring-bg"
                        cx="18"
                        cy="18"
                        r={CONTEXT_RING_RADIUS}
                        fill="none"
                        strokeWidth="4"
                      />
                      <circle
                        className="context-ring-value"
                        cx="18"
                        cy="18"
                        r={CONTEXT_RING_RADIUS}
                        fill="none"
                        strokeWidth="4"
                        strokeDasharray={contextRing.circumference}
                        strokeDashoffset={contextRing.dashOffset}
                      />
                    </svg>
                  </div>
                )}
                <button
                  type="button"
                  className="send-btn"
                  onClick={handleSend}
                  disabled={!currentConfig || !inputMsg.trim()}
                  aria-label="Send"
                  title="Send"
                >
                  {isStreamingCurrent ? (
                    <span className="send-spinner" aria-hidden="true" />
                  ) : (
                    <svg className="send-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path
                        d="M4 12l16-7-7 16-2.5-6L4 12z"
                        fill="currentColor"
                      />
                    </svg>
                  )}
                </button>
                {isStreamingCurrent && (
                  <button
                    type="button"
                    className="stop-btn"
                    onClick={handleStop}
                    aria-label="Stop"
                    title="Stop"
                  >
                    <svg className="stop-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {showDebugPanel && (
        <DebugPanel messages={messages} llmCalls={llmCalls} onClose={() => setShowDebugPanel(false)} />
      )}

      {showConfigManager && (
        <ConfigManager
          onClose={() => {
            setShowConfigManager(false);
            loadAllConfigs();
          }}
          onConfigCreated={() => {
            loadDefaultConfig();
            setSessionRefreshTrigger((prev) => prev + 1);
            loadAllConfigs();
          }}
        />
      )}

      <ConfirmDialog
        open={Boolean(rollbackTarget)}
        title="回撤消息"
        message="确定回撤到这条消息吗？"
        confirmLabel="回撤"
        cancelLabel="取消"
        danger
        onCancel={() => setRollbackTarget(null)}
        onConfirm={handleConfirmRollback}
      />

    </div>
    </div>
  );
}

export default App;
