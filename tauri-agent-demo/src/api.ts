import {
    LLMConfig,
    LLMConfigCreate,
    LLMConfigUpdate,
    ChatSession,
    ChatSessionCreate,
    ChatSessionUpdate,
    Message,
    ChatRequest,
    ChatResponse,
    ExportRequest
} from './types';

const API_BASE_URL = 'http://127.0.0.1:8000';

// ==================== LLM 配置 API ====================

export async function getConfigs(): Promise<LLMConfig[]> {
    const response = await fetch(`${API_BASE_URL}/configs`);
    if (!response.ok) throw new Error('Failed to fetch configs');
    return response.json();
}

export async function getDefaultConfig(): Promise<LLMConfig> {
    const response = await fetch(`${API_BASE_URL}/configs/default`);
    if (!response.ok) throw new Error('Failed to fetch default config');
    return response.json();
}

export async function getConfig(configId: string): Promise<LLMConfig> {
    const response = await fetch(`${API_BASE_URL}/configs/${configId}`);
    if (!response.ok) throw new Error('Failed to fetch config');
    return response.json();
}

export async function createConfig(config: LLMConfigCreate): Promise<LLMConfig> {
    const response = await fetch(`${API_BASE_URL}/configs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
    });
    if (!response.ok) throw new Error('Failed to create config');
    return response.json();
}

export async function updateConfig(configId: string, update: LLMConfigUpdate): Promise<LLMConfig> {
    const response = await fetch(`${API_BASE_URL}/configs/${configId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
    });
    if (!response.ok) throw new Error('Failed to update config');
    return response.json();
}

export async function deleteConfig(configId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/configs/${configId}`, {
        method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete config');
}

// ==================== 会话 API ====================

export async function getSessions(): Promise<ChatSession[]> {
    const response = await fetch(`${API_BASE_URL}/sessions`);
    if (!response.ok) throw new Error('Failed to fetch sessions');
    return response.json();
}

export async function getSession(sessionId: string): Promise<ChatSession> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}`);
    if (!response.ok) throw new Error('Failed to fetch session');
    return response.json();
}

export async function createSession(session: ChatSessionCreate): Promise<ChatSession> {
    const response = await fetch(`${API_BASE_URL}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(session),
    });
    if (!response.ok) throw new Error('Failed to create session');
    return response.json();
}

export async function updateSession(sessionId: string, update: ChatSessionUpdate): Promise<ChatSession> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
    });
    if (!response.ok) throw new Error('Failed to update session');
    return response.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}`, {
        method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete session');
}

export async function getSessionMessages(sessionId: string, limit?: number): Promise<Message[]> {
    const url = limit
        ? `${API_BASE_URL}/sessions/${sessionId}/messages?limit=${limit}`
        : `${API_BASE_URL}/sessions/${sessionId}/messages`;
    const response = await fetch(url);
    if (!response.ok) throw new Error('Failed to fetch messages');
    return response.json();
}

// ==================== 聊天 API ====================

export async function sendMessage(request: ChatRequest): Promise<ChatResponse> {
    const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error('Failed to send message');
    return response.json();
}

export async function* sendMessageStream(request: ChatRequest): AsyncGenerator<string, void, unknown> {
    const response = await fetch(`${API_BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });

    if (!response.ok) throw new Error('Failed to send stream');

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                try {
                    const parsed = JSON.parse(data);

                    // 如果包含session_id，yield整个对象
                    if (parsed.session_id || parsed.user_message_id) {
                        yield parsed;
                    }
                    // 如果标记done，结束
                    else if (parsed.done) {
                        return;
                    }
                    // 如果有content，yield内容字符串
                    else if (parsed.content) {
                        yield parsed.content;
                    }
                    // 如果有error，抛出异常
                    else if (parsed.error) {
                        throw new Error(parsed.error);
                    }
                } catch (e) {
                    if (e instanceof Error && e.message !== 'Unexpected end of JSON input') {
                        throw e;
                    }
                }
            }
        }
    }
}

// ==================== Agent 聊天 API ====================

export interface AgentStep {
    step_type: 'thought' | 'action' | 'observation' | 'answer' | 'error';
    content: string;
    metadata?: Record<string, any>;
}

export async function* sendMessageAgentStream(
    request: ChatRequest
): AsyncGenerator<AgentStep | { session_id: string; user_message_id?: number } | { done: true }, void, unknown> {
    const response = await fetch(`${API_BASE_URL}/chat/agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });

    if (!response.ok) throw new Error('Failed to send agent stream');

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                try {
                    const parsed = JSON.parse(data);

                    // 如果包含session_id，yield整个对象
                    if (parsed.session_id) {
                        yield parsed;
                    }
                    // 如果标记done，yield并结束
                    else if (parsed.done) {
                        yield parsed;
                        return;
                    }
                    // Agent步骤
                    else if (parsed.step_type) {
                        yield parsed as AgentStep;
                    }
                    // 如果有error，抛出异常
                    else if (parsed.error) {
                        throw new Error(parsed.error);
                    }
                } catch (e) {
                    if (e instanceof Error && e.message !== 'Unexpected end of JSON input') {
                        throw e;
                    }
                }
            }
        }
    }
}

// ==================== 导出 API ====================

export async function exportChatHistory(request: ExportRequest): Promise<Blob> {
    const response = await fetch(`${API_BASE_URL}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error('Failed to export chat history');
    return response.blob();
}
