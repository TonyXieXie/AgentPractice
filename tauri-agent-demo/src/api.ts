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
    SessionToolStats,
    ToolPermissionRequest,
    PatchRevertResponse,
    ToolDefinition,
    AgentPromptResponse,
    SkillSummary,
    AstRequest,
    AstPathSettings,
    AstSettingsResponse,
    AstSettingsAllResponse
} from './types';

export const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';

const normalizeBaseUrl = (value: string | undefined): string | undefined => {
    const trimmed = value?.trim();
    if (!trimmed) return undefined;
    return trimmed.replace(/\/+$/, '');
};

const envBaseUrl = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL);
export let API_BASE_URL = envBaseUrl ?? DEFAULT_API_BASE_URL;
let apiBaseUrlResolved = Boolean(envBaseUrl);
let apiBaseUrlPromise: Promise<string> | null = null;

export async function resolveApiBaseUrl(): Promise<string> {
    if (apiBaseUrlResolved) return API_BASE_URL;
    if (apiBaseUrlPromise) return apiBaseUrlPromise;
    apiBaseUrlPromise = (async () => {
        const envOverride = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL);
        if (envOverride) {
            API_BASE_URL = envOverride;
            apiBaseUrlResolved = true;
            return API_BASE_URL;
        }
        try {
            const { invoke } = await import('@tauri-apps/api/core');
            const resolved = await invoke<string>('get_backend_base_url');
            const normalized = normalizeBaseUrl(resolved);
            if (normalized) {
                API_BASE_URL = normalized;
            }
        } catch {
            // Keep default base URL when Tauri is unavailable.
        }
        apiBaseUrlResolved = true;
        return API_BASE_URL;
    })();
    return apiBaseUrlPromise;
}

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

export async function refreshMcpTools(): Promise<{ ok: boolean; registered?: string[] }> {
    const response = await fetch(`${API_BASE_URL}/mcp/refresh`, {
        method: 'POST',
    });
    if (!response.ok) {
        if (response.status === 404) {
            throw new Error('MCP refresh endpoint not found. Please restart the backend.');
        }
        throw await buildApiError(response, 'Failed to refresh MCP tools');
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

export async function getAgentPrompt(params?: {
    profileId?: string;
    sessionId?: string;
    includeTools?: boolean;
    agentType?: string;
}): Promise<AgentPromptResponse> {
    const query = new URLSearchParams();
    if (params?.profileId) query.set('profile_id', params.profileId);
    if (params?.sessionId) query.set('session_id', params.sessionId);
    if (params?.includeTools !== undefined) query.set('include_tools', params.includeTools ? 'true' : 'false');
    if (params?.agentType) query.set('agent_type', params.agentType);
    const suffix = query.toString();
    const response = await fetch(`${API_BASE_URL}/agent/prompt${suffix ? `?${suffix}` : ''}`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch agent prompt');
    }
    return response.json();
}

export async function getSkills(): Promise<SkillSummary[]> {
    const response = await fetch(`${API_BASE_URL}/skills`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch skills');
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

export async function notifyAstChanges(root: string, paths?: string[]): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/ast/notify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root, paths: paths || [] }),
    });
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to notify AST changes');
    }
}

export async function getAstCache(root: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ast/cache?root=${encodeURIComponent(root)}`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch AST cache');
    }
    return response.json();
}

export async function getAstCacheFile(root: string, path: string): Promise<any> {
    const query = `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`;
    const response = await fetch(`${API_BASE_URL}/ast/cache?${query}`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch AST cache file');
    }
    return response.json();
}

export async function getAstSettings(root: string): Promise<AstSettingsResponse> {
    const response = await fetch(`${API_BASE_URL}/ast/settings?root=${encodeURIComponent(root)}`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch AST settings');
    }
    return response.json();
}

export async function updateAstSettings(payload: AstPathSettings & { root: string }): Promise<AstSettingsResponse> {
    const response = await fetch(`${API_BASE_URL}/ast/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to update AST settings');
    }
    return response.json();
}

export async function getAstSettingsAll(): Promise<AstSettingsAllResponse> {
    const response = await fetch(`${API_BASE_URL}/ast/settings/all`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch AST settings');
    }
    return response.json();
}

export async function getCodeMap(sessionId: string, root: string): Promise<any> {
    const query = `session_id=${encodeURIComponent(sessionId)}&root=${encodeURIComponent(root)}`;
    const response = await fetch(`${API_BASE_URL}/ast/code-map?${query}`);
    if (!response.ok) {
        throw await buildApiError(response, 'Failed to fetch code map');
    }
    return response.json();
}

// ==================== Session API ====================

export async function getSessions(): Promise<ChatSession[]> {
    const response = await fetch(`${API_BASE_URL}/sessions`);
    if (!response.ok) throw new Error('Failed to fetch sessions');
    return response.json();
}

export async function getSession(
    sessionId: string,
    options?: { includeCount?: boolean }
): Promise<ChatSession> {
    const params = new URLSearchParams();
    if (options?.includeCount === false) {
        params.set('include_count', 'false');
    }
    const query = params.toString();
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}${query ? `?${query}` : ''}`);
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

export async function copySession(sessionId: string): Promise<ChatSession> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/copy`, {
        method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to copy session');
    return response.json();
}

export async function getSessionMessages(
    sessionId: string,
    options?: { limit?: number; beforeId?: number }
): Promise<Message[]> {
    const params = new URLSearchParams();
    if (options?.limit) {
        params.set('limit', String(options.limit));
    }
    if (options?.beforeId) {
        params.set('before_id', String(options.beforeId));
    }
    const query = params.toString();
    const url = query
        ? `${API_BASE_URL}/sessions/${sessionId}/messages?${query}`
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

export async function getSessionToolStats(sessionId: string): Promise<SessionToolStats> {
    const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/tool_stats`);
    if (!response.ok) throw new Error('Failed to fetch tool stats');
    return response.json();
}

export async function getSessionAgentSteps(
    sessionId: string,
    messageIds?: number[]
): Promise<AgentStepWithMessage[]> {
    const params = new URLSearchParams();
    if (messageIds && messageIds.length > 0) {
        params.set('message_ids', messageIds.join(','));
    }
    const query = params.toString();
    const url = query
        ? `${API_BASE_URL}/sessions/${sessionId}/agent_steps?${query}`
        : `${API_BASE_URL}/sessions/${sessionId}/agent_steps`;
    const response = await fetch(url);
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
    step_type: 'thought' | 'thought_delta' | 'action' | 'action_delta' | 'observation' | 'observation_delta' | 'answer' | 'answer_delta' | 'error' | 'context_estimate';
    content: string;
    metadata?: Record<string, any>;
}

export interface AgentStepWithMessage extends AgentStep {
    message_id: number;
    sequence?: number;
    timestamp?: string;
}

export interface PtyListItem {
    pty_id: string;
    status: string;
    pty: boolean;
    exit_code?: number | null;
    command?: string;
    pty_mode?: 'ephemeral' | 'persistent' | string;
    waiting_input?: boolean;
    wait_reason?: string | null;
    created_at?: number;
    idle_timeout?: number;
    buffer_size?: number;
    last_output_at?: number;
    seq?: number;
    screen_hash?: string;
}

export interface PtyReadResponse {
    pty_id: string;
    status: string;
    pty: boolean;
    exit_code?: number | null;
    command?: string;
    cursor: number;
    reset: boolean;
    chunk: string;
    waiting_input?: boolean;
    wait_reason?: string | null;
    screen_text?: string;
    screen_hash?: string;
    seq?: number;
    pty_mode?: 'ephemeral' | 'persistent' | string;
    pty_message_id?: number;
    pty_live?: boolean;
}

export interface AgentStreamKeepalive {
    keepalive: true;
}

export interface PtyStreamInitEvent {
    stream_id: string;
    session_id: string;
    seq?: number;
}

export interface PtyDeltaEvent {
    event: 'pty_delta';
    session_id: string;
    pty_id: string;
    chunk: string;
    cursor?: number;
    status?: 'running' | 'waiting_input' | 'exited' | 'closed' | 'error' | string;
    exit_code?: number | null;
    seq?: number;
    stream_seq?: number;
    pty_seq?: number;
    waiting_input?: boolean;
    wait_reason?: string | null;
    screen_hash?: string;
    pty_mode?: 'ephemeral' | 'persistent' | string;
    pty_message_id?: number | null;
    pty_live?: boolean;
}

export interface PtyStateEvent {
    event: 'pty_state';
    session_id: string;
    pty_id: string;
    chunk?: string;
    cursor?: number;
    status?: 'running' | 'waiting_input' | 'exited' | 'closed' | 'error' | string;
    exit_code?: number | null;
    seq?: number;
    stream_seq?: number;
    pty_seq?: number;
    waiting_input?: boolean;
    wait_reason?: string | null;
    screen_hash?: string;
    pty_mode?: 'ephemeral' | 'persistent' | string;
    pty_message_id?: number | null;
    pty_live?: boolean;
}

export interface PtyMessageUpsertSseEvent {
    event: 'pty_message_upsert';
    session_id: string;
    pty_id: string;
    message_id: number;
    content: string;
    status?: string;
    final: boolean;
    seq?: number;
    stream_seq?: number;
}

export interface PtyResyncRequiredEvent {
    event: 'pty_resync_required';
    session_id: string;
    pty_id?: string;
    reason?: string;
    seq?: number;
    stream_seq?: number;
}

export type PtySseEvent = PtyDeltaEvent | PtyStateEvent | PtyMessageUpsertSseEvent | PtyResyncRequiredEvent;

export interface PtyStreamKeepalive {
    keepalive: true;
}

export async function* sendMessageAgentStream(
    request: ChatRequest,
    signal?: AbortSignal
): AsyncGenerator<
    AgentStep | { session_id: string; user_message_id?: number; assistant_message_id?: number } | { done: true } | AgentStreamKeepalive,
    void,
    unknown
> {
    const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
    const maxReconnects = 6;
    let attempt = 0;
    let streamId: string | null = null;
    let lastSeq: number | null = null;

    while (true) {
        const resume = Boolean(streamId && attempt > 0);
        const payload: ChatRequest = {
            ...request,
            stream_id: resume ? streamId || undefined : undefined,
            last_seq: resume ? (lastSeq ?? undefined) : undefined,
            resume: resume ? true : undefined
        };

        let response: Response;
        try {
            response = await fetch(`${API_BASE_URL}/chat/agent/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal,
            });
        } catch (error) {
            if (signal?.aborted) throw error;
            if (!streamId || attempt >= maxReconnects) throw error;
            attempt += 1;
            const backoff = Math.min(30_000, 1000 * 2 ** (attempt - 1));
            await sleep(backoff);
            continue;
        }

        if (!response.ok) {
            let detail = `${response.status} ${response.statusText}`.trim();
            let detailText = '';
            try {
                const body = await response.text();
                if (body) {
                    detailText = body;
                    try {
                        const parsed = JSON.parse(body);
                        if (parsed?.detail) {
                            detailText = String(parsed.detail);
                        }
                    } catch {
                        // keep raw body
                    }
                    const compact = detailText.length > 800 ? `${detailText.slice(0, 800)}...(truncated)` : detailText;
                    detail = `${detail} | ${compact}`;
                }
            } catch {
                // ignore body read errors
            }
            if (resume && response.status === 404 && detailText.includes('Stream not found')) {
                streamId = null;
                lastSeq = null;
                attempt = 0;
                continue;
            }
            throw new Error(`Agent stream HTTP error: ${detail}`);
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let sawDone = false;

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split(/\r?\n/);
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line) continue;
                    if (line.startsWith(':')) {
                        yield { keepalive: true };
                        continue;
                    }
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        try {
                            const parsed = JSON.parse(data);

                            if (typeof parsed.stream_id === 'string') {
                                streamId = parsed.stream_id;
                            }
                            if (typeof parsed.seq === 'number') {
                                lastSeq = parsed.seq;
                            }

                            if (parsed.done) {
                                sawDone = true;
                                yield parsed;
                                return;
                            } else if (parsed.session_id) {
                                yield parsed;
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
        } catch (error) {
            if (signal?.aborted) throw error;
            if (!streamId || attempt >= maxReconnects) throw error;
            attempt += 1;
            const backoff = Math.min(30_000, 1000 * 2 ** (attempt - 1));
            await sleep(backoff);
            continue;
        }

        if (sawDone) return;
        if (signal?.aborted) return;
        if (!streamId || attempt >= maxReconnects) {
            throw new Error('Stream disconnected before completion.');
        }
        attempt += 1;
        const backoff = Math.min(30_000, 1000 * 2 ** (attempt - 1));
        await sleep(backoff);
    }
}

export async function* sendPtyStream(
    request: { session_id: string; stream_id?: string; last_seq?: number; resume?: boolean },
    signal?: AbortSignal
): AsyncGenerator<PtySseEvent | PtyStreamInitEvent | PtyStreamKeepalive, void, unknown> {
    const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
    const maxReconnects = 12;
    let attempt = 0;
    let streamId: string | null = request.stream_id || null;
    let lastSeq: number | null = Number.isFinite(request.last_seq as number) ? Number(request.last_seq) : null;

    while (true) {
        const resume = Boolean(streamId && attempt > 0);
        const payload: Record<string, any> = {
            session_id: request.session_id,
            stream_id: resume ? streamId || undefined : undefined,
            last_seq: resume ? (lastSeq ?? undefined) : (request.last_seq ?? undefined),
            resume: resume ? true : undefined
        };

        let response: Response;
        try {
            response = await fetch(`${API_BASE_URL}/pty/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal,
            });
        } catch (error) {
            if (signal?.aborted) throw error;
            if (!streamId || attempt >= maxReconnects) throw error;
            attempt += 1;
            await sleep(Math.min(30_000, 1000 * 2 ** (attempt - 1)));
            continue;
        }

        if (!response.ok) {
            let detail = `${response.status} ${response.statusText}`.trim();
            let detailText = '';
            try {
                const body = await response.text();
                if (body) {
                    detailText = body;
                    try {
                        const parsed = JSON.parse(body);
                        if (parsed?.detail) detailText = String(parsed.detail);
                    } catch {
                        // keep raw body
                    }
                    const compact = detailText.length > 800 ? `${detailText.slice(0, 800)}...(truncated)` : detailText;
                    detail = `${detail} | ${compact}`;
                }
            } catch {
                // ignore
            }
            if (resume && response.status === 404 && detailText.includes('PTY stream not found')) {
                streamId = null;
                lastSeq = null;
                attempt = 0;
                continue;
            }
            throw new Error(`PTY stream HTTP error: ${detail}`);
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split(/\r?\n/);
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line) continue;
                    if (line.startsWith(':')) {
                        yield { keepalive: true };
                        continue;
                    }
                    if (!line.startsWith('data: ')) continue;
                    const data = line.slice(6);
                    try {
                        const parsed = JSON.parse(data);
                        if (typeof parsed.stream_id === 'string') {
                            streamId = parsed.stream_id;
                            if (parsed.session_id) {
                                yield parsed as PtyStreamInitEvent;
                            }
                            continue;
                        }
                        const streamSeqCandidate =
                            typeof parsed.stream_seq === 'number'
                                ? parsed.stream_seq
                                : (typeof parsed.seq === 'number' ? parsed.seq : null);
                        if (typeof streamSeqCandidate === 'number') {
                            lastSeq = streamSeqCandidate;
                        }
                        const eventType =
                            typeof parsed.event === 'string'
                                ? parsed.event
                                : (typeof parsed.type === 'string' ? parsed.type : '');
                        if (
                            eventType === 'pty_delta' ||
                            eventType === 'pty_state' ||
                            eventType === 'pty_message_upsert' ||
                            eventType === 'pty_resync_required'
                        ) {
                            if (!parsed.event && parsed.type) {
                                parsed.event = parsed.type;
                            }
                            yield parsed as PtySseEvent;
                            continue;
                        }
                        if (parsed.error) {
                            throw new Error(parsed.error);
                        }
                    } catch (e) {
                        if (e instanceof Error && e.message !== 'Unexpected end of JSON input') {
                            throw e;
                        }
                    }
                }
            }
        } catch (error) {
            if (signal?.aborted) throw error;
            if (!streamId || attempt >= maxReconnects) throw error;
            attempt += 1;
            await sleep(Math.min(30_000, 1000 * 2 ** (attempt - 1)));
            continue;
        }

        if (signal?.aborted) return;
        if (!streamId || attempt >= maxReconnects) {
            throw new Error('PTY stream disconnected before recovery.');
        }
        attempt += 1;
        await sleep(Math.min(30_000, 1000 * 2 ** (attempt - 1)));
    }
}

export async function stopAgentStream(params: { messageId?: number; sessionId?: string }): Promise<{ stopped: boolean }> {
    const payload: Record<string, any> = {};
    if (typeof params.messageId === 'number') payload.message_id = params.messageId;
    if (typeof params.sessionId === 'string' && params.sessionId) payload.session_id = params.sessionId;
    const response = await fetch(`${API_BASE_URL}/chat/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
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

// ==================== PTY ====================

export async function listPtys(
    sessionId: string,
    options?: { includeExited?: boolean; maxExited?: number }
): Promise<PtyListItem[]> {
    if (!sessionId) return [];
    const params = new URLSearchParams({ session_id: sessionId });
    if (options?.includeExited !== undefined) {
        params.set('include_exited', options.includeExited ? 'true' : 'false');
    }
    if (options?.maxExited !== undefined) {
        params.set('max_exited', String(options.maxExited));
    }
    const response = await fetch(`${API_BASE_URL}/pty/list?${params.toString()}`);
    if (!response.ok) throw new Error('Failed to fetch PTY list');
    const data = await response.json();
    return Array.isArray(data?.items) ? data.items : [];
}

export async function readPty(payload: {
    session_id: string;
    pty_id: string;
    cursor?: number;
    max_output?: number;
}): Promise<PtyReadResponse> {
    const response = await fetch(`${API_BASE_URL}/pty/read`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('Failed to read PTY output');
    return response.json();
}

export async function sendPty(payload: {
    session_id: string;
    pty_id: string;
    input: string;
}): Promise<{ ok: boolean; pty_id: string; bytes_written: number }> {
    const response = await fetch(`${API_BASE_URL}/pty/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('Failed to send PTY input');
    return response.json();
}

export async function closePty(payload: {
    session_id: string;
    pty_id: string;
}): Promise<{ ok: boolean; pty_id: string }> {
    const response = await fetch(`${API_BASE_URL}/pty/close`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('Failed to close PTY');
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
