import {
    LLMConfig,
    LLMConfigCreate,
    LLMConfigUpdate,
    AppConfig,
    AppConfigUpdate,
    ChatSession,
    ChatSessionCreate,
    ChatSessionUpdate,
    Message,
    ChatRequest,
    ChatResponse,
    LLMCall,
    ToolPermissionRequest,
    PatchRevertResponse,
    ToolDefinition,
    AstRequest
} from './types';

export const API_BASE_URL = 'http://127.0.0.1:8000';

async function buildApiError(response: Response, baseMessage: string): Promise<Error> {
    const text = await response.text();
    let detail = text;
    if (text) {
        try {
            const data = JSON.parse(text);
            if (data?.detail) {
                detail = String(data.detail);
            }
        } catch {
            // keep raw text
        }
    }
    const suffix = detail ? `: ${detail}` : '';
    return new Error(`${baseMessage} (${response.status})${suffix}`);
}

// ==================== Config API ====================

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

export async function revertPatch(sessionId: string, revertPatch: string, messageId?: number): Promise<PatchRevertResponse> {
    const response = await fetch(`${API_BASE_URL}/patch/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, revert_patch: revertPatch, message_id: messageId }),
    });
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to revert patch');
    }
    return response.json();
}

export async function getAppConfig(): Promise<AppConfig> {
    const response = await fetch(`${API_BASE_URL}/app/config`);
    if (!response.ok) {
        if (response.status === 404) {
            throw new Error('App config endpoint not found. Please restart the backend.');
        }
        throw await buildApiError(response, 'Failed to fetch app config');
    }
    return response.json();
}

export async function updateAppConfig(update: AppConfigUpdate): Promise<AppConfig> {
    const response = await fetch(`${API_BASE_URL}/app/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
    });
    if (!response.ok) {
        if (response.status === 404) {
            throw new Error('App config endpoint not found. Please restart the backend.');
        }
        throw await buildApiError(response, 'Failed to update app config');
    }
    return response.json();
}

export async function getTools(): Promise<ToolDefinition[]> {
    const response = await fetch(`${API_BASE_URL}/tools`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch tools');
    }
    return response.json();
}

export async function runAstTool(payload: AstRequest): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/tools/ast`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to run AST tool');
    }
    return response.json();
}

// ==================== Session API ====================

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

export async function getSessionLLMCalls(sessionId: string): Promise<LLMCall[]> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/llm_calls`);
    if (!response.ok) throw new Error('Failed to fetch LLM calls');
    return response.json();
}

export async function getSessionAgentSteps(sessionId: string): Promise<AgentStepWithMessage[]> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/agent_steps`);
    if (!response.ok) throw new Error('Failed to fetch agent steps');
    return response.json();
}

// ==================== Chat API ====================

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
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                try {
                    const parsed = JSON.parse(data);

                    if (parsed.session_id || parsed.user_message_id) {
                        yield parsed;
                    } else if (parsed.done) {
                        return;
                    } else if (parsed.content) {
                        yield parsed.content;
                    } else if (parsed.error) {
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

// ==================== Agent Chat API ====================

export interface AgentStep {
    step_type: 'thought' | 'thought_delta' | 'action' | 'action_delta' | 'observation' | 'answer' | 'answer_delta' | 'error';
    content: string;
    metadata?: Record<string, any>;
}

export interface AgentStepWithMessage extends AgentStep {
    message_id: number;
    sequence?: number;
    timestamp?: string;
}

export async function* sendMessageAgentStream(
    request: ChatRequest,
    signal?: AbortSignal
): AsyncGenerator<
    AgentStep | { session_id: string; user_message_id?: number; assistant_message_id?: number } | { done: true },
    void,
    unknown
> {
    const response = await fetch(`${API_BASE_URL}/chat/agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal,
    });

    if (!response.ok) throw new Error('Failed to send agent stream');

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                try {
                    const parsed = JSON.parse(data);

                    if (parsed.session_id) {
                        yield parsed;
                    } else if (parsed.done) {
                        yield parsed;
                        return;
                    } else if (parsed.step_type) {
                        yield parsed as AgentStep;
                    } else if (parsed.error) {
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

export async function stopAgentStream(messageId: number): Promise<{ stopped: boolean }> {
    const response = await fetch(`${API_BASE_URL}/chat/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId })
    });
    if (!response.ok) throw new Error('Failed to stop stream');
    return response.json();
}

export interface RollbackResponse {
    session_id: string;
    input_message: string;
    remaining_messages: number;
}

export async function rollbackSession(sessionId: string, messageId: number): Promise<RollbackResponse> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId })
    });
    if (!response.ok) throw new Error('Failed to rollback session');
    return response.json();
}

// ==================== Tool Permissions ====================

export async function getToolPermissions(status?: string): Promise<ToolPermissionRequest[]> {
    const url = status
        ? `${API_BASE_URL}/tools/permissions?status=${encodeURIComponent(status)}`
        : `${API_BASE_URL}/tools/permissions`;
    const response = await fetch(url);
    if (!response.ok) throw new Error('Failed to fetch tool permissions');
    return response.json();
}

export async function updateToolPermission(requestId: number, status: string): Promise<ToolPermissionRequest> {
    const response = await fetch(`${API_BASE_URL}/tools/permissions/${requestId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status })
    });
    if (!response.ok) throw new Error('Failed to update tool permission');
    return response.json();
}

export function getAttachmentUrl(attachmentId: number, options?: { thumbnail?: boolean; maxSize?: number }): string {
    const params = new URLSearchParams();
    if (options?.thumbnail) {
        params.set('thumbnail', 'true');
    }
    if (options?.maxSize) {
        params.set('max_size', String(options.maxSize));
    }
    const query = params.toString();
    return query ? `${API_BASE_URL}/attachments/${attachmentId}?${query}` : `${API_BASE_URL}/attachments/${attachmentId}`;
}
