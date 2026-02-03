import { useState, useEffect, useRef } from 'react';
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
  getToolPermissions,
  updateToolPermission,
  updateConfig,
  AgentStep,
  AgentStepWithMessage,
} from './api';
import ConfigManager from './components/ConfigManager';
import SessionList from './components/SessionList';
import DebugPanel from './components/DebugPanel';
import AgentStepView from './components/AgentStepView';

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

function App() {
  const [inputMsg, setInputMsg] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentConfig, setCurrentConfig] = useState<LLMConfig | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [showConfigManager, setShowConfigManager] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sessionRefreshTrigger, setSessionRefreshTrigger] = useState(0);
  const [showSidebar, setShowSidebar] = useState(true);
  const [allConfigs, setAllConfigs] = useState<LLMConfig[]>([]);
  const [showConfigSelector, setShowConfigSelector] = useState(false);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const [llmCalls, setLlmCalls] = useState<LLMCall[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeAssistantIdRef = useRef<number | null>(null);
  const stopRequestedRef = useRef(false);
  const [pendingPermission, setPendingPermission] = useState<ToolPermissionRequest | null>(null);
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>('default');
  const [showReasoningSelector, setShowReasoningSelector] = useState(false);
  const [showAgentModeSelector, setShowAgentModeSelector] = useState(false);
  const [streamingSessionKey, setStreamingSessionKey] = useState<string | null>(null);
  const messagesCacheRef = useRef<Record<string, Message[]>>({});
  const currentSessionIdRef = useRef<string | null>(null);
  const streamingSessionKeyRef = useRef<string | null>(null);

  useEffect(() => {
    loadDefaultConfig();
    loadAllConfigs();
  }, []);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    if (autoScrollRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
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
    if (showDebugPanel && currentSessionId) {
      refreshSessionDebug(currentSessionId);
    }
  }, [showDebugPanel, currentSessionId, sessionRefreshTrigger]);

  useEffect(() => {
    if (!loading) {
      setPendingPermission(null);
      return;
    }
    let cancelled = false;
    let inFlight = false;

    const pollPermissions = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const pending = await getToolPermissions('pending');
        const shellRequest = pending.find((item) => item.tool_name === 'run_shell') || null;
        if (!cancelled) {
          setPendingPermission(shellRequest);
        }
      } catch (error) {
        if (!cancelled) {
          setPendingPermission(null);
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
  }, [loading]);

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

  const handleSend = async () => {
    if (!inputMsg.trim() || loading) return;
    if (!currentConfig) {
      alert('Please configure an LLM first.');
      return;
    }
    autoScrollRef.current = true;

    const userMessage = inputMsg.trim();
    setInputMsg('');
    setLoading(true);
    stopRequestedRef.current = false;
    activeAssistantIdRef.current = null;
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    const targetSessionId = currentSessionId;
    stashCurrentMessages();
    const sessionKey = getSessionKey(targetSessionId);
    let activeSessionKey = sessionKey;
    streamingSessionKeyRef.current = sessionKey;
    setStreamingSessionKey(sessionKey);

    const tempUserId = Date.now();
    const tempAssistantId = tempUserId + 1;
    let currentUserId = tempUserId;
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
        agent_action_buffers: {}
      }
    };

    updateSessionMessages(sessionKey, (prev) => [...prev, tempUserMsg, tempAssistantMsg]);

    try {
      const streamGenerator = sendMessageAgentStream(
        {
          message: userMessage,
          session_id: targetSessionId || undefined,
          config_id: currentConfig.id,
          agent_mode: agentMode,
        },
        abortController.signal
      );

      let newSessionId = targetSessionId;

      for await (const chunk of streamGenerator) {
        if (stopRequestedRef.current && !('session_id' in chunk)) {
          continue;
        }
        if ('session_id' in chunk && typeof chunk.session_id === 'string') {
          newSessionId = chunk.session_id;
          const incomingUserId = (chunk as any).user_message_id;
          const incomingAssistantId = (chunk as any).assistant_message_id;
          if (typeof incomingUserId === 'number') {
            currentUserId = incomingUserId;
          }
          if (typeof incomingAssistantId === 'number') {
            currentAssistantId = incomingAssistantId;
            activeAssistantIdRef.current = incomingAssistantId;
            if (stopRequestedRef.current) {
              stopAgentStream(incomingAssistantId).catch(() => undefined);
            }
          }
          if (incomingUserId || incomingAssistantId) {
            updateSessionMessages(activeSessionKey, (prev) =>
              prev.map((msg) => {
                if (typeof incomingUserId === 'number' && msg.id === tempUserId) {
                  return { ...msg, id: incomingUserId, session_id: newSessionId };
                }
                if (typeof incomingAssistantId === 'number' && msg.id === tempAssistantId) {
                  return { ...msg, id: incomingAssistantId, session_id: newSessionId };
                }
                if (!msg.session_id && newSessionId) {
                  return { ...msg, session_id: newSessionId };
                }
                return msg;
              })
            );
          }
          if (!targetSessionId) {
            currentSessionIdRef.current = newSessionId;
            setCurrentSessionId(newSessionId);
            setSessionRefreshTrigger((prev) => prev + 1);
          }
          if (activeSessionKey === DRAFT_SESSION_KEY && newSessionId) {
            const cached = messagesCacheRef.current[activeSessionKey] || [];
            delete messagesCacheRef.current[activeSessionKey];
            messagesCacheRef.current[newSessionId] = cached;
            activeSessionKey = newSessionId;
            streamingSessionKeyRef.current = newSessionId;
            setStreamingSessionKey(newSessionId);
          } else if (newSessionId) {
            activeSessionKey = newSessionId;
            streamingSessionKeyRef.current = newSessionId;
            setStreamingSessionKey(newSessionId);
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
            nextMetadata.agent_streaming = step.step_type !== 'answer' && step.step_type !== 'error';
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
      if (error?.name === 'AbortError' || stopRequestedRef.current) {
        // User stopped streaming
      } else {
        console.error('Failed to send message:', error);
        const errorMsg: Message = {
          id: Date.now() + 2,
          session_id: targetSessionId || '',
          role: 'assistant',
          content: `Chat error: ${error.message || 'Please check whether the backend is running.'}`,
          timestamp: new Date().toISOString(),
        };
        updateSessionMessages(activeSessionKey, (prev) => [...prev.filter((m) => m.id !== currentAssistantId), errorMsg]);
      }
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
      streamingSessionKeyRef.current = null;
      setStreamingSessionKey(null);
    }
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
    if (!loading) return;
    stopRequestedRef.current = true;
    let assistantId = activeAssistantIdRef.current;
    if (assistantId) {
      try {
        await stopAgentStream(assistantId);
      } catch (error) {
        console.error('Failed to stop stream:', error);
      }
    }
    updateSessionMessages(getCurrentSessionKey(), (prev) => {
      if (!assistantId) {
        const lastAssistant = [...prev].reverse().find((msg) => msg.role === 'assistant');
        assistantId = lastAssistant?.id ?? null;
      }
      if (!assistantId) return prev;
      return prev.map((msg) => (msg.id === assistantId ? applyStopNoteToMessage(msg) : msg));
    });
    setLoading(false);
    streamingSessionKeyRef.current = null;
    setStreamingSessionKey(null);
  };

  const handlePermissionDecision = async (status: 'approved' | 'denied') => {
    if (!pendingPermission || permissionBusy) return;
    setPermissionBusy(true);
    try {
      await updateToolPermission(pendingPermission.id, status);
      setPendingPermission(null);
    } catch (error) {
      console.error('Failed to update permission:', error);
      alert('权限更新失败');
    } finally {
      setPermissionBusy(false);
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

      const cached = messagesCacheRef.current[sessionId];
      const isStreamingSession = streamingSessionKeyRef.current === sessionId;
      const hasStreaming = Boolean(cached?.some((msg) => msg.metadata?.agent_streaming));
      if (cached && (isStreamingSession || hasStreaming)) {
        setSessionMessages(sessionId, cached);
      }

      const [session, calls] = await Promise.all([
        getSession(sessionId),
        getSessionLLMCalls(sessionId),
      ]);

      setLlmCalls(calls);

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

        setSessionMessages(sessionId, hydratedMessages);
      }

      const config = await getConfig(session.config_id);
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load session:', error);
      alert('Failed to load session.');
    }
  };

  const handleNewChat = () => {
    stashCurrentMessages();
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    setSessionMessages(DRAFT_SESSION_KEY, []);
    setLlmCalls([]);
    autoScrollRef.current = true;
  };

  const handleRollback = async (messageId: number) => {
    if (!currentSessionId) return;
    if (loading) {
      alert('请先停止当前输出再回撤。');
      return;
    }
    if (!confirm('确定回撤到这条消息吗？')) return;

    try {
      const result = await rollbackSession(currentSessionId, messageId);
      await handleSelectSession(currentSessionId);
      setInputMsg(result.input_message || '');
      inputRef.current?.focus();
      setSessionRefreshTrigger((prev) => prev + 1);
      await refreshSessionDebug(currentSessionId);
    } catch (error) {
      console.error('Failed to rollback session:', error);
      alert('回撤失败');
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
  const isStreamingCurrent = Boolean(loading && streamingSessionKey === currentSessionKey);
  const currentReasoning = (currentConfig?.reasoning_effort || 'medium') as ReasoningEffort;

  return (
    <div className="app-container">
      {showSidebar && (
        <SessionList
          currentSessionId={currentSessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
          refreshTrigger={sessionRefreshTrigger}
        />
      )}

      <div className="main-content">
        <div className="chat-container">
          <div className="chat-header">
            <div className="header-left">
              <button
                className="sidebar-toggle"
                onClick={() => setShowSidebar(!showSidebar)}
                title={showSidebar ? 'Hide sidebar' : 'Show sidebar'}
              >
                {showSidebar ? '<' : '>'}
              </button>
              <h1>Agent Desktop Demo</h1>
            </div>

            <div className="header-right">
              <button
                className="header-btn"
                onClick={() => setShowConfigManager(true)}
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
                className={`header-btn ${showDebugPanel ? 'active' : ''}`}
                onClick={() => setShowDebugPanel(!showDebugPanel)}
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

          <div className="messages" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>Welcome to Agent Chat</h2>
                <p>Type a message to get started.</p>
                {!currentConfig && <p className="warning">Please configure an LLM.</p>}
              </div>
            ) : (
              messages.map((msg) => {
                const steps = (msg.metadata?.agent_steps || []) as AgentStep[];
                const streaming = Boolean(msg.metadata?.agent_streaming);
                const showPermission = Boolean(pendingPermission && msg.id === latestAssistantId);
                return (
                  <div key={msg.id} className={`message ${msg.role}`}>
                    <div className="message-content">
                      {msg.role === 'assistant' && (steps.length > 0 || showPermission) ? (
                        <AgentStepView
                          steps={steps}
                          streaming={streaming}
                          pendingPermission={showPermission ? pendingPermission : null}
                          onPermissionDecision={handlePermissionDecision}
                          permissionBusy={permissionBusy}
                        />
                      ) : msg.role === 'user' ? (
                        <>
                          <div className="message-text">{msg.content}</div>
                          <button
                            className="message-action-btn icon inline"
                            onClick={() => handleRollback(msg.id)}
                            title="回撤到此消息"
                            aria-label="回撤到此消息"
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
              disabled={!currentConfig || loading}
              ref={inputRef}
            />

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
                      <span className="dropdown-arrow">▾</span>
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
                      disabled={!currentConfig || loading}
                      aria-label={`Agent mode: ${agentMode}`}
                      title={`Agent模式: ${agentMode}`}
                    >
                      <span className="selector-text">
                        {AGENT_MODE_OPTIONS.find((opt) => opt.value === agentMode)?.label || agentMode}
                      </span>
                      <span className="dropdown-arrow">▾</span>
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
                      disabled={!currentConfig || loading}
                      aria-label={`Reasoning: ${currentReasoning}`}
                      title={`Reasoning: ${currentReasoning}`}
                    >
                      <span className="selector-text">{currentReasoning}</span>
                      <span className="dropdown-arrow">▾</span>
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
                </div>
              )}

              <div className="input-actions">
                <button
                  type="button"
                  className="send-btn"
                  onClick={handleSend}
                  disabled={!currentConfig || loading || !inputMsg.trim()}
                  aria-label="Send"
                  title="Send"
                >
                  {loading ? (
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

    </div>
  );
}

export default App;
