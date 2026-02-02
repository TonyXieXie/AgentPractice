import { useState, useEffect, useRef } from 'react';
import './App.css';
import { Message, LLMConfig, LLMCall } from './types';
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
  AgentStep,
  AgentStepWithMessage,
} from './api';
import ConfigManager from './components/ConfigManager';
import SessionList from './components/SessionList';
import DebugPanel from './components/DebugPanel';
import AgentStepView from './components/AgentStepView';

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

  useEffect(() => {
    loadDefaultConfig();
    loadAllConfigs();
  }, []);

  useEffect(() => {
    if (autoScrollRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (showConfigSelector && !target.closest('.config-selector-wrapper')) {
        setShowConfigSelector(false);
      }
    };

    if (showConfigSelector) {
      document.addEventListener('click', handleClickOutside);
    }

    return () => {
      document.removeEventListener('click', handleClickOutside);
    };
  }, [showConfigSelector]);

  useEffect(() => {
    if (showDebugPanel && currentSessionId) {
      refreshSessionDebug(currentSessionId);
    }
  }, [showDebugPanel, currentSessionId, sessionRefreshTrigger]);

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

    setMessages((prev) => [...prev, tempUserMsg, tempAssistantMsg]);

    try {
      const streamGenerator = sendMessageAgentStream(
        {
          message: userMessage,
          session_id: targetSessionId || undefined,
          config_id: currentConfig.id,
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
            setMessages((prev) =>
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
            setCurrentSessionId(newSessionId);
            setSessionRefreshTrigger((prev) => prev + 1);
          }
          continue;
        }

        if ('done' in chunk) {
          break;
        }

        const step = chunk as AgentStep;

        setMessages((prev) =>
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

      setMessages((prev) =>
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
        setMessages((prev) => [...prev.filter((m) => m.id !== currentAssistantId), errorMsg]);
      }
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
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
    setMessages((prev) => {
      if (!assistantId) {
        const lastAssistant = [...prev].reverse().find((msg) => msg.role === 'assistant');
        assistantId = lastAssistant?.id ?? null;
      }
      if (!assistantId) return prev;
      return prev.map((msg) => (msg.id === assistantId ? applyStopNoteToMessage(msg) : msg));
    });
    setLoading(false);
  };

  const handleSelectSession = async (sessionId: string) => {
    try {
      setCurrentSessionId(sessionId);
      const [msgs, session, calls, steps] = await Promise.all([
        getSessionMessages(sessionId),
        getSession(sessionId),
        getSessionLLMCalls(sessionId),
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

      setMessages(hydratedMessages);
      setLlmCalls(calls);

      const config = await getConfig(session.config_id);
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load session:', error);
      alert('Failed to load session.');
    }
  };

  const handleNewChat = () => {
    setCurrentSessionId(null);
    setMessages([]);
    setLlmCalls([]);
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
              >
                Config
              </button>

              <button
                className={`header-btn ${showDebugPanel ? 'active' : ''}`}
                onClick={() => setShowDebugPanel(!showDebugPanel)}
                title="Debug"
              >
                Debug
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
                return (
                  <div key={msg.id} className={`message ${msg.role}`}>
                    <div className="message-content">
                      {msg.role === 'assistant' && steps.length > 0 ? (
                        <AgentStepView steps={steps} streaming={streaming} />
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
            {loading && (
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

            {currentConfig && (
              <div className="model-selector-inline">
                <button
                  className="model-selector-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowConfigSelector(!showConfigSelector);
                  }}
                >
                  <span>Model</span>
                  <span>{currentConfig.name}</span>
                  <span>{showConfigSelector ? '^' : 'v'}</span>
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
            )}

            <button
              type="button"
              onClick={handleSend}
              disabled={!currentConfig || loading || !inputMsg.trim()}
            >
              {loading ? 'Sending...' : 'Send'}
            </button>
            {loading && (
              <button
                type="button"
                className="stop-btn"
                onClick={handleStop}
              >
                Stop
              </button>
            )}
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
