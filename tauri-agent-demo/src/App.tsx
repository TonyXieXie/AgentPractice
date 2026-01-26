import { useState } from "react";
// import { invoke } from "@tauri-apps/api/core";
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
      // 2. Call Python FastAPI Backend (Sidecar Mode Simulation)
      // fetch is standard JS API. We call localhost:8000 where our Python server is running.
      const response = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: inputMsg }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      // 3. Add Agent Response
      const agentMsg: Message = { id: Date.now() + 1, role: 'agent', content: data.reply };
      setMessages(prev => [...prev, agentMsg]);
    } catch (e) {
      console.error(e);
      const errorMsg: Message = { id: Date.now() + 1, role: 'agent', content: "Error connecting to Python backend. Is main.py running?" };
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
