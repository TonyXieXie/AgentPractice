import { useState, useEffect, useRef } from "react";
import "./App.css";
import { Message, LLMConfig, ChatSession } from './types';
import { sendMessage, getDefaultConfig, getConfig, getSessionMessages, exportChatHistory } from './api';
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

    // 乐观更新：立即显示用户消息
    const tempUserMsg: Message = {
      id: Date.now(),
      session_id: currentSessionId || '',
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString()
    };
    setMessages(prev => [...prev, tempUserMsg]);

    try {
      const response = await sendMessage({
        message: userMessage,
        session_id: currentSessionId || undefined,
        config_id: currentConfig?.id
      });

      // 如果是新会话，设置会话ID
      if (!currentSessionId) {
        setCurrentSessionId(response.session_id);
        setSessionRefreshTrigger(prev => prev + 1);
      }

      // 重新加载完整的消息列表（包含调试数据）
      const updatedMessages = await getSessionMessages(response.session_id);
      setMessages(updatedMessages);

      setSessionRefreshTrigger(prev => prev + 1);
    } catch (error: any) {
      console.error('Failed to send message:', error);
      const errorMsg: Message = {
        id: Date.now() + 1,
        session_id: currentSessionId || '',
        role: 'assistant',
        content: `❌ 发送失败: ${error.message || '请检查后端服务是否运行，以及配置是否正确'}`,
        timestamp: new Date().toISOString()
      };
      setMessages(prev => [...prev, errorMsg]);
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
              {currentConfig ? (
                <div className="config-selector-wrapper">
                  <button
                    className="config-info clickable"
                    onClick={() => setShowConfigSelector(!showConfigSelector)}
                    title="切换模型"
                  >
                    🤖 {currentConfig.name}
                  </button>

                  {showConfigSelector && (
                    <div className="config-dropdown">
                      {allConfigs.map((config) => (
                        <div
                          key={config.id}
                          className={`config-option ${config.id === currentConfig.id ? 'active' : ''}`}
                          onClick={() => handleSwitchConfig(config.id)}
                        >
                          <div className="config-option-name">{config.name}</div>
                          <div className="config-option-meta">
                            {config.api_type.toUpperCase()} · {config.model}
                          </div>
                        </div>
                      ))}
                      {allConfigs.length === 0 && (
                        <div className="config-option disabled">暂无配置</div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="config-info">⚠️ 未配置</div>
              )}

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
            <button
              type="button"
              onClick={handleSend}
              disabled={!currentConfig || loading || !inputMsg.trim()}
            >
              {loading ? '发送中...' : '发送'}
            </button>
          </div>
        </div>

        <p className="footer-text">
          Powered by Tauri + React + FastAPI
        </p>
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
