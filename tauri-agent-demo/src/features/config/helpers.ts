import type { AgentConfig, MCPConfig, MCPServerConfig, ToolDefinition } from '../../types';

export type MCPServerForm = {
    enabled: boolean;
    server_label: string;
    server_url: string;
    connector_id: string;
    server_description: string;
    authorization_env: string;
    headers_env: string;
};

export const ABILITY_TYPE_OPTIONS: { value: string; label: string }[] = [
    { value: 'tooling', label: '工具' },
    { value: 'tool_policy', label: '工具策略' },
    { value: 'output_format', label: '输出要求' },
    { value: 'workflow', label: '工作流' },
    { value: 'constraints', label: '约束' },
    { value: 'persona', label: '角色' },
    { value: 'domain_knowledge', label: '领域知识' },
    { value: 'localization', label: '本地化' },
    { value: 'examples', label: '示例' }
];

export const normalizeTools = (tools: unknown): ToolDefinition[] => {
    if (!Array.isArray(tools)) return [];
    const normalized: ToolDefinition[] = [];
    const seen = new Set<string>();
    for (const item of tools) {
        let name = '';
        let description: string | undefined;
        let parameters: Record<string, any>[] | undefined;
        if (typeof item === 'string') {
            name = item.trim();
        } else if (item && typeof item === 'object') {
            const candidate = item as Partial<ToolDefinition> & { name?: unknown };
            if (typeof candidate.name === 'string') {
                name = candidate.name.trim();
                description = typeof candidate.description === 'string' ? candidate.description : undefined;
                parameters = Array.isArray(candidate.parameters) ? candidate.parameters : undefined;
            }
        }
        if (!name || seen.has(name)) continue;
        seen.add(name);
        normalized.push({ name, description, parameters });
    }
    return normalized;
};

export const isMcpTool = (tool: ToolDefinition) => {
    const name = (tool.name || '').trim();
    const desc = (tool.description || '').trim().toLowerCase();
    return name.startsWith('mcp__') || desc.startsWith('mcp:');
};

export const filterLocalTools = (tools: ToolDefinition[]) => tools.filter((tool) => !isMcpTool(tool));

export const formatToolAbilityName = (toolName: string) => {
    const cleaned = toolName
        .replace(/[_-]+/g, ' ')
        .trim();
    if (!cleaned) return toolName;
    return cleaned.replace(/\b\w/g, (letter) => letter.toUpperCase());
};

export const isRecord = (value: unknown): value is Record<string, any> =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value));

export const buildExportName = (prefix: string) => {
    const stamp = new Date().toISOString().slice(0, 10);
    return `${prefix}-${stamp}.json`;
};

export const coerceNumber = (value: unknown, fallback: number) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

export const coerceInt = (value: unknown, fallback: number) => {
    const parsed = Number.parseInt(String(value), 10);
    return Number.isFinite(parsed) ? parsed : fallback;
};

export const stripAgentGlobalFields = (agent: AgentConfig): AgentConfig => {
    if (!agent || typeof agent !== 'object') return {};
    const { react_max_iterations, ast_enabled, code_map, mcp, ...rest } = agent as Record<string, any>;
    return rest as AgentConfig;
};

export const MODEL_IMPORT_KINDS = new Set(['models', 'llm_configs', 'configs']);
export const GLOBAL_IMPORT_KINDS = new Set(['global', 'app', 'app_config']);
export const AGENT_IMPORT_KINDS = new Set(['agent', 'agent_config']);

export const isAllowedKind = (kind: string | undefined, allowed: Set<string>) =>
    !kind || allowed.has(kind);

export const ALL_IMPORT_KINDS = new Set(['all', 'bundle', 'config_bundle', 'full']);

export const normalizeAstImportList = (value: unknown): string[] => {
    if (Array.isArray(value)) {
        return value
            .filter((item): item is string => typeof item === 'string')
            .map((item) => item.trim())
            .filter(Boolean);
    }
    if (typeof value === 'string') {
        return value
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean);
    }
    return [];
};

export const normalizeAstImportLanguages = (value: unknown): string[] => {
    if (Array.isArray(value)) {
        return value
            .filter((item): item is string => typeof item === 'string')
            .map((item) => item.trim().toLowerCase())
            .filter(Boolean);
    }
    if (typeof value === 'string') {
        return value
            .split(/[,\r\n]+/)
            .map((item) => item.trim().toLowerCase())
            .filter(Boolean);
    }
    return [];
};

export const normalizeMcpServers = (mcp?: MCPConfig | null): MCPServerForm[] => {
    const servers = Array.isArray(mcp?.servers) ? (mcp?.servers as MCPServerConfig[]) : [];
    return servers.map((server) => {
        return {
            enabled: server.enabled ?? true,
            server_label: server.server_label || '',
            server_url: server.server_url || '',
            connector_id: server.connector_id || '',
            server_description: server.server_description || '',
            authorization_env: server.authorization_env || '',
            headers_env: server.headers_env || ''
        };
    });
};

export const buildMcpServersPayload = (
    servers: MCPServerForm[]
): { servers: MCPServerConfig[]; error?: string } => {
    const payload: MCPServerConfig[] = [];
    const labels = new Set<string>();

    for (let index = 0; index < servers.length; index += 1) {
        const server = servers[index];
        const label = server.server_label.trim();
        if (!label) {
            return { servers: payload, error: `MCP 服务器 ${index + 1} 的 server_label 不能为空。` };
        }
        if (labels.has(label)) {
            return { servers: payload, error: `MCP 服务器 server_label 不能重复: ${label}` };
        }
        labels.add(label);

        const serverUrl = server.server_url.trim();
        const connectorId = server.connector_id.trim();
        if (!serverUrl) {
            return {
                servers: payload,
                error: `MCP 服务器 ${label} 需要填写 server_url（connector_id 已不支持）。`
            };
        }

        const item: MCPServerConfig = {
            server_label: label,
            enabled: Boolean(server.enabled)
        };
        if (serverUrl) item.server_url = serverUrl;
        if (connectorId) item.connector_id = connectorId;

        const description = server.server_description.trim();
        if (description) item.server_description = description;
        const authEnv = server.authorization_env.trim();
        if (authEnv) item.authorization_env = authEnv;
        const headersEnv = server.headers_env.trim();
        if (headersEnv) item.headers_env = headersEnv;

        payload.push(item);
    }

    return { servers: payload };
};

export const createEmptyMcpServer = (): MCPServerForm => ({
    enabled: true,
    server_label: '',
    server_url: '',
    connector_id: '',
    server_description: '',
    authorization_env: '',
    headers_env: ''
});

export const sanitizeMcpSegment = (value: string, fallback: string) => {
    const cleaned = value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '');
    return cleaned || fallback;
};

export const getMcpToolPrefix = (serverLabel: string) => {
    const safeServer = sanitizeMcpSegment(serverLabel || '', 'server');
    return `mcp__${safeServer}__`;
};

export const splitMcpToolDescription = (tool: ToolDefinition): { title: string; description: string } => {
    const name = tool.name || '';
    const desc = tool.description || '';
    if (desc.startsWith('mcp:') && desc.includes(' - ')) {
        const [title, ...rest] = desc.split(' - ');
        return { title: title.trim() || name, description: rest.join(' - ').trim() };
    }
    return { title: name, description: desc };
};
