import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

// Interface for messages
interface Message {
  id: number;
  role: 'user' | 'agent';
  content: string;
}

function App() {
  const [inputMsg, setInputMsg] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    { id: 1, role: 'agent', content: 'Hello! I am your local Agent powered by Tauri. How can I help you today?' }
  ]);

  async function handleSend() {
    if (!inputMsg.trim()) return;

    // 1. Add User Message
    const userMsg: Message = { id: Date.now(), role: 'user', content: inputMsg };
    setMessages(prev => [...prev, userMsg]);
    setInputMsg("");

    try {
      // 2. Call Rust Backend (Simulating AI processing)
      // The default template has a 'greet' command. We'll use it to simulate a response.
      // In a real app, this would call 'chat_with_llm'
      const response = await invoke<string>("greet", { name: inputMsg });

      // 3. Add Agent Response
      const agentMsg: Message = { id: Date.now() + 1, role: 'agent', content: response + " (Processed by Rust Core)" };
      setMessages(prev => [...prev, agentMsg]);
    } catch (e) {
      console.error(e);
      const errorMsg: Message = { id: Date.now() + 1, role: 'agent', content: "Error connecting to Rust backend." };
      setMessages(prev => [...prev, errorMsg]);
    }
  }

  return (
    <div className="container">
      <div className="chat-container">

        {/* Header */}
        <div className="chat-header">
          <h1>🤖 Agent Desktop Demo</h1>
          <div className="status-dot" title="Core Online"></div>
        </div>

        {/* Message List */}
        <div className="messages">
          {messages.map((msg) => (
            <div key={msg.id} className={`message ${msg.role}`}>
              {msg.content}
            </div>
          ))}
        </div>

        {/* Input Area */}
        <div className="input-area">
          <input
            onChange={(e) => setInputMsg(e.currentTarget.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            value={inputMsg}
            placeholder="Type a message to the Agent..."
          />
          <button type="button" onClick={handleSend}>
            Send
          </button>
        </div>

      </div>

      <p style={{ marginTop: '20px', color: '#666', fontSize: '0.8rem' }}>
        Powered by Tauri + React + Vite
      </p>
    </div>
  );
}

export default App;
