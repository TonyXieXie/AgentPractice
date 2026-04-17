import type { ChatRequest } from '../../../types';
import { API_BASE_URL } from './base';

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

export interface AgentStreamResyncRequired {
    resync_required: true;
    reason?: string;
    stream_id?: string;
    latest_seq?: number;
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

                            if (parsed.resync_required) {
                                const reason = String(parsed.reason || 'unknown');
                                const error = new Error(`Agent stream resync required: ${reason}`);
                                (error as any).code = 'AGENT_STREAM_RESYNC_REQUIRED';
                                (error as any).latestSeq = parsed.latest_seq;
                                throw error;
                            } else if (parsed.done) {
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

