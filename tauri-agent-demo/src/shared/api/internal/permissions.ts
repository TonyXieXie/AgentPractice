import type { ToolPermissionRequest } from '../../../types';
import { API_BASE_URL } from './base';

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

