import type {
  ChatSession,
  ChatSessionCreate,
  ChatSessionUpdate,
  LLMCall,
  Message,
  SessionToolStats,
} from '../../../types';
import { API_BASE_URL } from './base';
import type { AgentStepWithMessage } from './streaming';

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

