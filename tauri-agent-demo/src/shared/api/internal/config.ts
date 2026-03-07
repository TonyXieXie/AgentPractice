import type {
  AgentPromptResponse,
  AppConfig,
  AppConfigUpdate,
  AstPathSettings,
  AstRequest,
  AstSettingsAllResponse,
  AstSettingsResponse,
  LLMConfig,
  LLMConfigCreate,
  LLMConfigUpdate,
  PatchRevertResponse,
  SkillSummary,
  ToolDefinition,
} from '../../../types';
import { API_BASE_URL, buildApiError } from './base';

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
    if (!response.ok) throw await buildApiError(response, 'Failed to create config');
    return response.json();
}

export async function updateConfig(configId: string, update: LLMConfigUpdate): Promise<LLMConfig> {
    const response = await fetch(`${API_BASE_URL}/configs/${configId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
    });
    if (!response.ok) throw await buildApiError(response, 'Failed to update config');
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

