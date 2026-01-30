import { useState, useEffect, useRef } from "react";
import "./App.css";
import { Message, LLMConfig, ChatSession } from './types';
import { sendMessageAgentStream, getDefaultConfig, getConfig, getSessionMessages, exportChatHistory, AgentStep } from "./api";
import ConfigManager from './components/ConfigManager';
import SessionList from './components/SessionList';
import DebugPanel from './components/DebugPanel';

function App() {
  const [inputMsg, setInputMsg] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentConfig, setCurrentConfig] = useState<LLMConfig | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [showConfigManager, setShowConfigManager] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sessionRefreshTrigger, setSessionRefreshTrigger] = useState(0);
  const [showSidebar, setShowSidebar] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [allConfigs, setAllConfigs] = useState<LLMConfig[]>([]); // 所有配置列表
  const [showConfigSelector, setShowConfigSelector] = useState(false); // 显示配置选择器
  const [showDebugPanel, setShowDebugPanel] = useState(false); // 显示Debug面板

  useEffect(() => {
    loadDefaultConfig();
    loadAllConfigs(); // 加载所有配置
  }, []);

  // 自动滚动到最新消息
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 点击外部关闭配置选择器
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

  const loadDefaultConfig = async () => {
    try {
      const config = await getDefaultConfig();
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load default config:', error);
      // 如果没有默认配置，显示配置管理器
      setShowConfigManager(true);
    }
  };

  const loadAllConfigs = async () => {
    try {
      const configs = await fetch('http://127.0.0.1:8000/configs').then(r => r.json());
      setAllConfigs(configs);
    } catch (error) {
      console.error('Failed to load all configs:', error);
    }
  };

  const handleSwitchConfig = async (configId: string) => {
    try {
      const config = await getConfig(configId);
      setCurrentConfig(config);
      setShowConfigSelector(false);
    } catch (error) {
      console.error('Failed to switch config:', error);
      alert('切换配置失败');
    }
  };

  const handleSend = async () => {
    if (!inputMsg.trim() || loading) return;

    const userMessage = inputMsg;
    setInputMsg("");
    setLoading(true);

    // 捕获当前会话ID
    const targetSessionId = currentSessionId;

    // 构建将要发送的请求（用于debug显示）
    const raw_request = {
      model: currentConfig?.model || "unknown",
      messages: [
        { role: "system", content: "你是一个有帮助的AI助手。" },
        { role: "user", content: userMessage }
      ],
      temperature: currentConfig?.temperature || 0.7,
      max_tokens: currentConfig?.max_tokens || 2000,
      stream: true,
      api_type: currentConfig?.api_type || "unknown",
      agent_type: "react"  // 标记使用 Agent 模式
    };

    // 乐观更新：立即显示用户消息（包含raw_request用于debug）
    const tempUserMsg: Message = {
      id: Date.now(),
      session_id: targetSessionId || '',
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
      raw_request: raw_request  // 添加原始请求数据
    };
    setMessages(prev => [...prev, tempUserMsg]);

    // 临时助手消息（流式更新）
    const tempAssistantId = Date.now() + 1;
    const tempAssistantMsg: Message = {
      id: tempAssistantId,
      session_id: targetSessionId || '',
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString()
    };
    setMessages(prev => [...prev, tempAssistantMsg]);

    try {
      // 🔥 使用 Agent 流式接口
      const streamGenerator = sendMessageAgentStream({
        message: userMessage,
        session_id: targetSessionId || undefined,
        config_id: currentConfig?.id
      });

      let fullContent = '';
      let newSessionId = targetSessionId;
      let agentSteps: string[] = []; // 收集 Agent 步骤用于显示
      let allStepsMetadata: any[] = []; // 收集所有步骤的元数据用于 debug

      for await (const chunk of streamGenerator) {
        // 处理 session_id
        if ('session_id' in chunk && typeof chunk.session_id === 'string') {
          newSessionId = chunk.session_id;
          if (!targetSessionId) {
            setCurrentSessionId(newSessionId);
            setSessionRefreshTrigger(prev => prev + 1);
          }
          continue;
        }

        // 处理 done 信号
        if ('done' in chunk) {
          break;
        }

        // 处理 Agent 步骤
        const step = chunk as AgentStep;
        allStepsMetadata.push(step); // 保存步骤元数据
        
        if (step.step_type === 'thought') {
          // 💭 思考步骤
          const thoughtText = `💭 **思考**: ${step.content}\n\n`;
          agentSteps.push(thoughtText);
          fullContent = agentSteps.join('') + '⏳ 正在处理...';
          
          setMessages(prev => prev.map(msg =>
            msg.id === tempAssistantId
              ? { ...msg, content: fullContent }
              : msg
          ));
        } 
        else if (step.step_type === 'action') {
          // 🔧 行动步骤
          const actionText = `🔧 **行动**: ${step.content}\n\n`;
          agentSteps.push(actionText);
          fullContent = agentSteps.join('') + '⏳ 执行工具...';
          
          setMessages(prev => prev.map(msg =>
            msg.id === tempAssistantId
              ? { ...msg, content: fullContent }
              : msg
          ));
        } 
        else if (step.step_type === 'observation') {
          // 👁️ 观察步骤
          const observationText = `👁️ **观察**: ${step.content}\n\n`;
          agentSteps.push(observationText);
          fullContent = agentSteps.join('') + '⏳ 继续推理...';
          
          setMessages(prev => prev.map(msg =>
            msg.id === tempAssistantId
              ? { ...msg, content: fullContent }
              : msg
          ));
        } 
        else if (step.step_type === 'answer') {
          // ✅ 最终答案
          const answerText = `\n---\n\n✅ **最终答案**:\n\n${step.content}`;
          agentSteps.push(answerText);
          fullContent = agentSteps.join('');
          
          setMessages(prev => prev.map(msg =>
            msg.id === tempAssistantId
              ? { ...msg, content: fullContent }
              : msg
          ));
        } 
        else if (step.step_type === 'error') {
          // ❌ 错误
          const errorText = `❌ **错误**: ${step.content}\n\n`;
          agentSteps.push(errorText);
          fullContent = agentSteps.join('');
          
          setMessages(prev => prev.map(msg =>
            msg.id === tempAssistantId
              ? { ...msg, content: fullContent }
              : msg
          ));
        }
      }

      // 🔥 流式结束后，添加 raw_response 用于 debug，但保持前端显示的格式化内容
      const raw_response = {
        agent_type: "react",
        steps: allStepsMetadata,
        final_content: fullContent,
        model: currentConfig?.model || "unknown"
      };

      // 更新助手消息，添加 raw_response
      setMessages(prev => prev.map(msg =>
        msg.id === tempAssistantId
          ? { ...msg, raw_response: raw_response }
          : msg
      ));

      // 更新会话刷新触发器
      if (newSessionId) {
        setSessionRefreshTrigger(prev => prev + 1);
      }

    } catch (error: any) {
      console.error('Failed to send message:', error);
      const errorMsg: Message = {
        id: Date.now() + 2,
        session_id: targetSessionId || '',
        role: 'assistant',
        content: `❌ 聊天错误: ${error.message || '请检查后端服务是否运行'}`,
        timestamp: new Date().toISOString()
      };
      setMessages(prev => [...prev.filter(m => m.id !== tempAssistantId), errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    try {
      setCurrentSessionId(sessionId);
      const msgs = await getSessionMessages(sessionId);
      setMessages(msgs);

      // 加载该会话使用的配置
      const session = await fetch(`http://127.0.0.1:8000/sessions/${sessionId}`).then(r => r.json()) as ChatSession;
      const config = await getConfig(session.config_id);
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load session:', error);
      alert('加载会话失败');
    }
  };

  const handleNewChat = () => {
    setCurrentSessionId(null);
    setMessages([]);
  };

  const handleExportChat = async () => {
    try {
      const blob = await exportChatHistory({
        session_id: currentSessionId || undefined,
        format: 'markdown'
      });

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `chat_export_${new Date().getTime()}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to export:', error);
      alert('导出失败');
    }
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
          {/* Header */}
          <div className="chat-header">
            <div className="header-left">
              <button
                className="sidebar-toggle"
                onClick={() => setShowSidebar(!showSidebar)}
                title={showSidebar ? "隐藏侧边栏" : "显示侧边栏"}
              >
                {showSidebar ? '◀' : '▶'}
              </button>
              <h1>🤖 Agent Desktop Demo</h1>
            </div>

            <div className="header-right">

              <button
                className="header-btn"
                onClick={handleExportChat}
                disabled={!currentSessionId}
                title="导出当前会话"
              >
                💾
              </button>

              <button
                className="header-btn"
                onClick={() => setShowConfigManager(true)}
                title="配置管理"
              >
                ⚙️
              </button>

              <button
                className={`header-btn ${showDebugPanel ? 'active' : ''}`}
                onClick={() => setShowDebugPanel(!showDebugPanel)}
                title="Debug 调试"
              >
                🐛
              </button>
            </div>
          </div>

          {/* Message List */}
          <div className="messages">
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>👋 欢迎使用 Agent Chat</h2>
                <p>输入消息开始对话...</p>
                {!currentConfig && (
                  <p className="warning">⚠️ 请先配置 LLM</p>
                )}
              </div>
            ) : (
              messages.map((msg) => (
                <div key={msg.id} className={`message ${msg.role}`}>
                  <div className="message-content">{msg.content}</div>
                  <div className="message-time">
                    {new Date(msg.timestamp).toLocaleTimeString('zh-CN')}
                  </div>
                </div>
              ))
            )}
            {loading && (
              <div className="message assistant loading">
                <div className="message-content">
                  <span className="typing-indicator">
                    <span></span><span></span><span></span>
                  </span>
                </div>
              </div>
            )}
            {/* 滚动锚点 */}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div className="input-area">
            <input
              onChange={(e) => setInputMsg(e.currentTarget.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              value={inputMsg}
              placeholder={currentConfig ? "输入消息..." : "请先配置 LLM"}
              disabled={!currentConfig || loading}
            />

            {/* Model Selector Below Input - Left Side */}
            {currentConfig && (
              <div className="model-selector-inline">
                <button
                  className="model-selector-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowConfigSelector(!showConfigSelector);
                  }}
                >
                  <span>🤖</span>
                  <span>{currentConfig.name}</span>
                  <span>{showConfigSelector ? '▲' : '▼'}</span>
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
                        <div className="config-meta">{config.api_type.toUpperCase()} · {config.model}</div>
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
              {loading ? '发送中...' : '发送'}
            </button>
          </div>
        </div>
      </div>

      {showDebugPanel && (
        <DebugPanel
          messages={messages}
          onClose={() => setShowDebugPanel(false)}
        />
      )}

      {showConfigManager && (
        <ConfigManager
          onClose={() => {
            setShowConfigManager(false);
            loadAllConfigs(); // 关闭时刷新配置列表
          }}
          onConfigCreated={() => {
            loadDefaultConfig();
            setSessionRefreshTrigger(prev => prev + 1);
            loadAllConfigs(); // 创建配置后刷新列表
          }}
        />
      )}
    </div>
  );
}

export default App;
