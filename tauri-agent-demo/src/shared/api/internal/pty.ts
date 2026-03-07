import { API_BASE_URL } from './base';
import type { PtyListItem, PtyReadResponse } from './streaming';

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

