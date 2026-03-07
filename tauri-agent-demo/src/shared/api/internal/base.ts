

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

function formatApiErrorDetail(detail: unknown): string {
    if (typeof detail === 'string') return detail;
    if (detail === null || detail === undefined) return '';
    if (Array.isArray(detail)) {
        return detail
            .map((item) => formatApiErrorDetail(item))
            .filter(Boolean)
            .join('; ');
    }
    if (typeof detail === 'object') {
        const record = detail as Record<string, unknown>;
        const loc = Array.isArray(record.loc)
            ? record.loc.map((item) => String(item)).join('.')
            : '';
        const msg = typeof record.msg === 'string' ? record.msg : '';
        const type = typeof record.type === 'string' ? record.type : '';
        const parts = [loc, msg, type].filter(Boolean);
        if (parts.length > 0) {
            return parts.join(': ');
        }
        try {
            return JSON.stringify(detail);
        } catch {
            return String(detail);
        }
    }
    return String(detail);
}

export async function buildApiError(response: Response, baseMessage: string): Promise<Error> {
    const text = await response.text();
    let detail = text;
    if (text) {
        try {
            const data = JSON.parse(text);
            if (data?.detail !== undefined) {
                detail = formatApiErrorDetail(data.detail);
            } else if (data?.message !== undefined) {
                detail = formatApiErrorDetail(data.message);
            }
        } catch {
            // keep raw text
        }
    }
    const suffix = detail ? `: ${detail}` : '';
    return new Error(`${baseMessage} (${response.status})${suffix}`);
}

