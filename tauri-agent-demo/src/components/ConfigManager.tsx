import { useState, useEffect } from 'react';
import { save as saveDialog } from '@tauri-apps/plugin-dialog';
import { writeTextFile } from '@tauri-apps/plugin-fs';
import {
    LLMConfig,
    LLMConfigCreate,
    LLMApiFormat,
    LLMProfile,
    AppConfig,
    AppConfigUpdate,
    AgentConfig,
    AgentAbility,
    AgentProfile,
    ToolDefinition
} from '../types';
import {
    getConfigs,
    createConfig,
    updateConfig,
    deleteConfig,
    getAppConfig,
    updateAppConfig,
    getTools,
    getAstSettingsAll,
    updateAstSettings
} from '../api';
import { exportConfigFile, importConfigFile } from '../configExchange';
import ConfirmDialog from './ConfirmDialog';
import './ConfigManager.css';

interface ConfigManagerProps {
    onClose: () => void;
    onConfigCreated?: () => void;
}

const FORMAT_OPTIONS: { value: LLMApiFormat; label: string }[] = [
    { value: 'openai_chat_completions', label: 'OpenAI Chat Completions' },
    { value: 'openai_responses', label: 'OpenAI Responses' }
];

const PROFILE_OPTIONS: { value: LLMProfile; label: string }[] = [
    { value: 'openai', label: 'OpenAI' },
    { value: 'openai_compatible', label: 'OpenAI-Compatible' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'zhipu', label: 'Zhipu (GLM)' }
];

const DEFAULT_CONTEXT_START_PCT = 75;
const DEFAULT_CONTEXT_TARGET_PCT = 55;
const DEFAULT_CONTEXT_MIN_KEEP_MESSAGES = 12;
const DEFAULT_CONTEXT_KEEP_RECENT_CALLS = 10;
const DEFAULT_CONTEXT_STEP_CALLS = 5;
const DEFAULT_CONTEXT_LONG_THRESHOLD = 4000;
const DEFAULT_CONTEXT_LONG_HEAD_CHARS = 1200;
const DEFAULT_CONTEXT_LONG_TAIL_CHARS = 800;

const ABILITY_TYPE_OPTIONS: { value: string; label: string }[] = [
    { value: 'tooling', label: '工具说明' },
    { value: 'tool_policy', label: '工具策略' },
    { value: 'output_format', label: '输出要求' },
    { value: 'workflow', label: '工作流' },
    { value: 'constraints', label: '约束' },
    { value: 'persona', label: '角色' },
    { value: 'domain_knowledge', label: '领域知识' },
    { value: 'localization', label: '本地化' },
    { value: 'examples', label: '示例' }
];

const normalizeTools = (tools: unknown): ToolDefinition[] => {
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

const isRecord = (value: unknown): value is Record<string, any> =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value));

const buildExportName = (prefix: string) => {
    const stamp = new Date().toISOString().slice(0, 10);
    return `${prefix}-${stamp}.json`;
};

const coerceNumber = (value: unknown, fallback: number) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const coerceInt = (value: unknown, fallback: number) => {
    const parsed = Number.parseInt(String(value), 10);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const stripAgentGlobalFields = (agent: AgentConfig): AgentConfig => {
    if (!agent || typeof agent !== 'object') return {};
    const { react_max_iterations, ast_enabled, code_map, ...rest } = agent as Record<string, any>;
    return rest as AgentConfig;
};

const MODEL_IMPORT_KINDS = new Set(['models', 'llm_configs', 'configs']);
const GLOBAL_IMPORT_KINDS = new Set(['global', 'app', 'app_config']);
const AGENT_IMPORT_KINDS = new Set(['agent', 'agent_config']);

const isAllowedKind = (kind: string | undefined, allowed: Set<string>) =>
    !kind || allowed.has(kind);

const ALL_IMPORT_KINDS = new Set(['all', 'bundle', 'config_bundle', 'full']);

const normalizeAstImportList = (value: unknown): string[] => {
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

const normalizeAstImportLanguages = (value: unknown): string[] => {
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

export default function ConfigManager({ onClose, onConfigCreated }: ConfigManagerProps) {
    type ConfigTab = 'models' | 'global' | 'agents';
    const [configs, setConfigs] = useState<LLMConfig[]>([]);
    const [activeTab, setActiveTab] = useState<ConfigTab>('models');
    const [globalTimeoutSec, setGlobalTimeoutSec] = useState('180');
    const [globalReactMaxIterations, setGlobalReactMaxIterations] = useState('50');
    const [globalAstEnabled, setGlobalAstEnabled] = useState(true);
    const [globalCodeMapEnabled, setGlobalCodeMapEnabled] = useState(true);
    const [globalContextCompressionEnabled, setGlobalContextCompressionEnabled] = useState(false);
    const [globalContextCompressStartPct, setGlobalContextCompressStartPct] = useState(
        String(DEFAULT_CONTEXT_START_PCT)
    );
    const [globalContextCompressTargetPct, setGlobalContextCompressTargetPct] = useState(
        String(DEFAULT_CONTEXT_TARGET_PCT)
    );
    const [globalContextMinKeepMessages, setGlobalContextMinKeepMessages] = useState(
        String(DEFAULT_CONTEXT_MIN_KEEP_MESSAGES)
    );
    const [globalContextKeepRecentCalls, setGlobalContextKeepRecentCalls] = useState(
        String(DEFAULT_CONTEXT_KEEP_RECENT_CALLS)
    );
    const [globalContextStepCalls, setGlobalContextStepCalls] = useState(
        String(DEFAULT_CONTEXT_STEP_CALLS)
    );
    const [globalContextTruncateLongData, setGlobalContextTruncateLongData] = useState(true);
    const [globalContextLongThreshold, setGlobalContextLongThreshold] = useState(
        String(DEFAULT_CONTEXT_LONG_THRESHOLD)
    );
    const [globalContextLongHeadChars, setGlobalContextLongHeadChars] = useState(
        String(DEFAULT_CONTEXT_LONG_HEAD_CHARS)
    );
    const [globalContextLongTailChars, setGlobalContextLongTailChars] = useState(
        String(DEFAULT_CONTEXT_LONG_TAIL_CHARS)
    );
    const [globalLoading, setGlobalLoading] = useState(false);
    const [globalSaving, setGlobalSaving] = useState(false);
    const [globalSaved, setGlobalSaved] = useState(false);
    const [agentConfig, setAgentConfig] = useState<AgentConfig>({});
    const [agentLoading, setAgentLoading] = useState(false);
    const [agentSaving, setAgentSaving] = useState(false);
    const [agentSaved, setAgentSaved] = useState(false);
    const [availableTools, setAvailableTools] = useState<ToolDefinition[]>([]);
    const [showAbilityForm, setShowAbilityForm] = useState(false);
    const [editingAbilityId, setEditingAbilityId] = useState<string | null>(null);
    const [abilityForm, setAbilityForm] = useState({
        name: '',
        type: 'tooling',
        prompt: '',
        tools: [] as string[],
        paramsText: ''
    });
    const [abilityFormError, setAbilityFormError] = useState<string | null>(null);
    const [showProfileForm, setShowProfileForm] = useState(false);
    const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
    const [profileForm, setProfileForm] = useState({
        name: '',
        abilities: [] as string[],
        paramsText: '',
        isDefault: false
    });
    const [profileFormError, setProfileFormError] = useState<string | null>(null);
    const [showForm, setShowForm] = useState(false);
    const [editingConfig, setEditingConfig] = useState<LLMConfig | null>(null);
    const [loading, setLoading] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState<LLMConfig | null>(null);
    const [bundleBusy, setBundleBusy] = useState(false);

    const [formData, setFormData] = useState<LLMConfigCreate>({
        name: '',
        api_format: 'openai_chat_completions',
        api_profile: 'openai',
        api_key: '',
        base_url: '',
        model: '',
        temperature: 0.7,
        max_tokens: 2000,
        max_context_tokens: 200000,
        is_default: false,
    });

    const normalizeAgentConfig = (data?: AgentConfig | null): AgentConfig => {
        const raw = isRecord(data) ? data : {};
        const parsedMax = Number.parseInt(String(raw.react_max_iterations ?? ''), 10);
        const reactMaxIterations = Number.isFinite(parsedMax) ? Math.min(200, Math.max(1, parsedMax)) : 50;
        return {
            ...raw,
            base_system_prompt: raw.base_system_prompt ?? '',
            react_max_iterations: reactMaxIterations,
            abilities: Array.isArray(raw.abilities) ? raw.abilities : [],
            profiles: Array.isArray(raw.profiles) ? raw.profiles : [],
            default_profile: raw.default_profile ?? '',
        };
    };

    useEffect(() => {
        loadConfigs();
        loadAppConfig();
        loadTools();
    }, []);

    const loadConfigs = async () => {
        try {
            const data = await getConfigs();
            setConfigs(data);
        } catch (error) {
            console.error('Failed to load configs:', error);
            alert('Failed to load configs.');
        }
    };

    const applyAppConfigState = (data?: AppConfig | null) => {
        const timeoutValue = data?.llm?.timeout_sec;
        if (timeoutValue !== undefined && timeoutValue !== null) {
            setGlobalTimeoutSec(String(timeoutValue));
        }
        const reactMax = data?.agent?.react_max_iterations;
        if (reactMax !== undefined && reactMax !== null) {
            setGlobalReactMaxIterations(String(reactMax));
        }
        setGlobalAstEnabled(Boolean(data?.agent?.ast_enabled ?? true));
        setGlobalCodeMapEnabled(Boolean(data?.agent?.code_map?.enabled ?? true));
        const contextConfig = data?.context || {};
        setGlobalContextCompressionEnabled(Boolean(contextConfig?.compression_enabled));
        const startPct = Number.parseInt(
            String(contextConfig?.compress_start_pct ?? DEFAULT_CONTEXT_START_PCT),
            10
        );
        if (Number.isFinite(startPct)) {
            setGlobalContextCompressStartPct(String(startPct));
        }
        const targetPct = Number.parseInt(
            String(contextConfig?.compress_target_pct ?? DEFAULT_CONTEXT_TARGET_PCT),
            10
        );
        if (Number.isFinite(targetPct)) {
            setGlobalContextCompressTargetPct(String(targetPct));
        }
        const minKeep = Number.parseInt(
            String(contextConfig?.min_keep_messages ?? DEFAULT_CONTEXT_MIN_KEEP_MESSAGES),
            10
        );
        if (Number.isFinite(minKeep)) {
            setGlobalContextMinKeepMessages(String(minKeep));
        }
        const keepCalls = Number.parseInt(
            String(contextConfig?.keep_recent_calls ?? DEFAULT_CONTEXT_KEEP_RECENT_CALLS),
            10
        );
        if (Number.isFinite(keepCalls)) {
            setGlobalContextKeepRecentCalls(String(keepCalls));
        }
        const stepCalls = Number.parseInt(
            String(contextConfig?.step_calls ?? DEFAULT_CONTEXT_STEP_CALLS),
            10
        );
        if (Number.isFinite(stepCalls)) {
            setGlobalContextStepCalls(String(stepCalls));
        }
        setGlobalContextTruncateLongData(Boolean(contextConfig?.truncate_long_data ?? true));
        const longThreshold = Number.parseInt(
            String(contextConfig?.long_data_threshold ?? DEFAULT_CONTEXT_LONG_THRESHOLD),
            10
        );
        if (Number.isFinite(longThreshold)) {
            setGlobalContextLongThreshold(String(longThreshold));
        }
        const longHead = Number.parseInt(
            String(contextConfig?.long_data_head_chars ?? DEFAULT_CONTEXT_LONG_HEAD_CHARS),
            10
        );
        if (Number.isFinite(longHead)) {
            setGlobalContextLongHeadChars(String(longHead));
        }
        const longTail = Number.parseInt(
            String(contextConfig?.long_data_tail_chars ?? DEFAULT_CONTEXT_LONG_TAIL_CHARS),
            10
        );
        if (Number.isFinite(longTail)) {
            setGlobalContextLongTailChars(String(longTail));
        }
        setAgentConfig(normalizeAgentConfig(data?.agent));
    };

    const loadAppConfig = async () => {
        setGlobalLoading(true);
        try {
            const data = await getAppConfig();
            applyAppConfigState(data);
        } catch (error: any) {
            console.error('Failed to load app config:', error);
            let errorMessage = 'Failed to load global config';
            if (error?.message) {
                errorMessage += `: ${error.message}`;
            }
            if (error?.message?.includes('fetch')) {
                errorMessage += '\n\nPlease verify:\n1) Backend is running\n2) http://127.0.0.1:8000 is reachable';
            }
            alert(errorMessage);
        } finally {
            setGlobalLoading(false);
        }
    };

    const loadTools = async () => {
        try {
            const tools = await getTools();
            setAvailableTools(normalizeTools(tools));
        } catch (error) {
            console.error('Failed to load tools:', error);
        }
    };

    const extractConfigImportItems = (data: unknown): unknown[] => {
        if (Array.isArray(data)) return data;
        if (isRecord(data)) {
            if (Array.isArray(data.configs)) return data.configs;
            if (Array.isArray(data.models)) return data.models;
            if (Array.isArray(data.items)) return data.items;
        }
        return [];
    };

    const makeUniqueName = (base: string, used: Set<string>) => {
        let candidate = base;
        let index = 2;
        while (used.has(candidate)) {
            candidate = `${base} (import ${index})`;
            index += 1;
        }
        used.add(candidate);
        return candidate;
    };

    const buildImportedConfigPayload = (
        raw: unknown,
        index: number,
        usedNames: Set<string>
    ): { payload?: Record<string, any>; error?: string } => {
        if (!isRecord(raw)) {
            return { error: '配置格式不正确' };
        }
        const rawName = typeof raw.name === 'string' && raw.name.trim()
            ? raw.name.trim()
            : '';
        const model = typeof raw.model === 'string' ? raw.model.trim() : '';
        if (!model) {
            const fallbackName = rawName || `Imported Config ${index + 1}`;
            return { error: `配置 "${fallbackName}" 缺少 model` };
        }
        const name = rawName || makeUniqueName(`Imported Config ${index + 1}`, usedNames);
        if (rawName) {
            usedNames.add(rawName);
        }

        const payload: Record<string, any> = { ...raw };
        payload.name = name;
        payload.api_format = typeof raw.api_format === 'string' && raw.api_format.trim()
            ? raw.api_format
            : 'openai_chat_completions';
        const profile = typeof raw.api_profile === 'string' && raw.api_profile.trim()
            ? raw.api_profile
            : (typeof raw.api_type === 'string' && raw.api_type.trim() ? raw.api_type : '');
        payload.api_profile = profile || 'openai';
        if (typeof raw.api_key === 'string') {
            payload.api_key = raw.api_key;
        }
        if (typeof raw.base_url === 'string') {
            payload.base_url = raw.base_url;
        } else {
            delete payload.base_url;
        }
        payload.model = model;
        payload.temperature = coerceNumber(raw.temperature, 0.7);
        payload.max_tokens = Math.round(coerceNumber(raw.max_tokens, 2000));
        payload.max_context_tokens = Math.round(coerceNumber(raw.max_context_tokens, 200000));
        payload.is_default = Boolean(raw.is_default);
        if (typeof raw.reasoning_effort === 'string') {
            payload.reasoning_effort = raw.reasoning_effort;
        }
        if (typeof raw.reasoning_summary === 'string') {
            payload.reasoning_summary = raw.reasoning_summary;
        }

        delete payload.id;
        delete payload.created_at;
        return { payload };
    };

    const handleExportConfigs = async () => {
        try {
            const ok = await exportConfigFile('models', { configs }, {
                title: '导出模型配置',
                defaultName: buildExportName('model-configs'),
            });
            if (ok) alert('模型配置已导出。');
        } catch (error: any) {
            console.error('Failed to export configs:', error);
            alert(`导出失败: ${error?.message || 'Unknown error'}`);
        }
    };

    const handleImportConfigs = async () => {
        let result;
        try {
            result = await importConfigFile({ title: '导入模型配置' });
        } catch (error: any) {
            console.error('Failed to import configs:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
            return;
        }
        if (!result) return;
        if (!isAllowedKind(result.kind, MODEL_IMPORT_KINDS)) {
            alert(`导入失败: 文件类型为 "${result.kind}"，不是模型配置。`);
            return;
        }

        const items = extractConfigImportItems(result.data);
        if (items.length === 0) {
            alert('未找到可导入的模型配置。');
            return;
        }
        const resultSummary = await importModelConfigsFromItems(items, configs);
        if (resultSummary.cancelled) return;
        await loadConfigs();
        onConfigCreated?.();

        const summary = `导入完成：成功 ${resultSummary.success}，失败 ${resultSummary.failed.length}，跳过 ${resultSummary.skipped.length}`;
        const details = [
            ...resultSummary.failed.map((item) => `失败: ${item}`),
            ...resultSummary.skipped.map((item) => `跳过: ${item}`)
        ];
        const detailText = details.length ? `\n${details.slice(0, 4).join('\n')}` : '';
        alert(`${summary}${detailText}`);
    };

    const buildGlobalExportPayload = (): AppConfigUpdate => ({
        llm: {
            timeout_sec: coerceNumber(globalTimeoutSec, 180),
        },
        agent: {
            react_max_iterations: coerceInt(globalReactMaxIterations, 50),
            ast_enabled: globalAstEnabled,
            code_map: {
                enabled: globalCodeMapEnabled,
            },
        },
        context: {
            compression_enabled: globalContextCompressionEnabled,
            compress_start_pct: coerceInt(globalContextCompressStartPct, DEFAULT_CONTEXT_START_PCT),
            compress_target_pct: coerceInt(globalContextCompressTargetPct, DEFAULT_CONTEXT_TARGET_PCT),
            min_keep_messages: coerceInt(globalContextMinKeepMessages, DEFAULT_CONTEXT_MIN_KEEP_MESSAGES),
            keep_recent_calls: coerceInt(globalContextKeepRecentCalls, DEFAULT_CONTEXT_KEEP_RECENT_CALLS),
            step_calls: coerceInt(globalContextStepCalls, DEFAULT_CONTEXT_STEP_CALLS),
            truncate_long_data: globalContextTruncateLongData,
            long_data_threshold: coerceInt(globalContextLongThreshold, DEFAULT_CONTEXT_LONG_THRESHOLD),
            long_data_head_chars: coerceInt(globalContextLongHeadChars, DEFAULT_CONTEXT_LONG_HEAD_CHARS),
            long_data_tail_chars: coerceInt(globalContextLongTailChars, DEFAULT_CONTEXT_LONG_TAIL_CHARS),
        },
    });

    const buildGlobalExportFromAppConfig = (appConfig: AppConfig | null | undefined): AppConfigUpdate => {
        if (!appConfig) return {};
        const payload: AppConfigUpdate = {};
        if (appConfig.llm) {
            payload.llm = { timeout_sec: appConfig.llm.timeout_sec };
        }
        if (appConfig.context) {
            payload.context = appConfig.context;
        }
        if (appConfig.agent) {
            const agent: Record<string, any> = { ...appConfig.agent };
            delete agent.abilities;
            delete agent.profiles;
            delete agent.default_profile;
            delete agent.base_system_prompt;
            if (Object.keys(agent).length > 0) {
                payload.agent = agent;
            }
        }
        return payload;
    };

    const extractGlobalImportPayload = (data: unknown): AppConfigUpdate | null => {
        if (!isRecord(data)) return null;
        const candidate = isRecord(data.app_config)
            ? data.app_config
            : isRecord(data.appConfig)
                ? data.appConfig
                : isRecord(data.config)
                    ? data.config
                    : data;
        if (!isRecord(candidate)) return null;

        const payload: AppConfigUpdate = {};
        if (isRecord(candidate.llm)) {
            payload.llm = candidate.llm;
        }
        if (isRecord(candidate.context)) {
            payload.context = candidate.context;
        }
        if (isRecord(candidate.agent)) {
            const agent: Record<string, any> = { ...candidate.agent };
            delete agent.abilities;
            delete agent.profiles;
            delete agent.default_profile;
            delete agent.base_system_prompt;
            if (Object.keys(agent).length > 0) {
                payload.agent = agent;
            }
        }
        return Object.keys(payload).length > 0 ? payload : null;
    };

    const handleExportGlobalConfig = async () => {
        try {
            const ok = await exportConfigFile('global', buildGlobalExportPayload(), {
                title: '导出全局配置',
                defaultName: buildExportName('global-config'),
            });
            if (ok) alert('全局配置已导出。');
        } catch (error: any) {
            console.error('Failed to export global config:', error);
            alert(`导出失败: ${error?.message || 'Unknown error'}`);
        }
    };

    const handleImportGlobalConfig = async () => {
        let result;
        try {
            result = await importConfigFile({ title: '导入全局配置' });
        } catch (error: any) {
            console.error('Failed to import global config:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
            return;
        }
        if (!result) return;
        if (!isAllowedKind(result.kind, GLOBAL_IMPORT_KINDS)) {
            alert(`导入失败: 文件类型为 "${result.kind}"，不是全局配置。`);
            return;
        }
        const payload = extractGlobalImportPayload(result.data);
        if (!payload) {
            alert('未识别到有效的全局配置。');
            return;
        }
        if (!globalSaved) {
            const proceed = window.confirm('当前有未保存的全局配置修改，导入将覆盖它们。是否继续？');
            if (!proceed) return;
        }
        setGlobalSaving(true);
        setGlobalSaved(false);
        try {
            const updated = await updateAppConfig(payload);
            applyAppConfigState(updated);
            setGlobalSaved(true);
            onConfigCreated?.();
            alert('全局配置已导入。');
        } catch (error: any) {
            console.error('Failed to import global config:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
        } finally {
            setGlobalSaving(false);
        }
    };

    const extractAgentImportPayload = (data: unknown): AgentConfig | null => {
        if (!isRecord(data)) return null;
        const candidate = isRecord(data.agent) ? data.agent : data;
        if (!isRecord(candidate)) return null;
        return stripAgentGlobalFields(candidate as AgentConfig);
    };

    const handleExportAgentConfig = async () => {
        try {
            const payload = stripAgentGlobalFields(normalizeAgentConfig(agentConfig));
            const ok = await exportConfigFile('agent', payload, {
                title: '导出 Agent 配置',
                defaultName: buildExportName('agent-config'),
            });
            if (ok) alert('Agent 配置已导出。');
        } catch (error: any) {
            console.error('Failed to export agent config:', error);
            alert(`导出失败: ${error?.message || 'Unknown error'}`);
        }
    };

    const handleImportAgentConfig = async () => {
        let result;
        try {
            result = await importConfigFile({ title: '导入 Agent 配置' });
        } catch (error: any) {
            console.error('Failed to import agent config:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
            return;
        }
        if (!result) return;
        if (!isAllowedKind(result.kind, AGENT_IMPORT_KINDS)) {
            alert(`导入失败: 文件类型为 "${result.kind}"，不是 Agent 配置。`);
            return;
        }
        const payload = extractAgentImportPayload(result.data);
        if (!payload) {
            alert('未识别到有效的 Agent 配置。');
            return;
        }
        if (!agentSaved) {
            const proceed = window.confirm('当前有未保存的 Agent 配置修改，导入将覆盖它们。是否继续？');
            if (!proceed) return;
        }
        setAgentSaving(true);
        setAgentSaved(false);
        try {
            const merged = mergeAgentConfigByName(agentConfig, payload);
            const updated = await updateAppConfig({ agent: merged });
            applyAppConfigState(updated);
            setAgentSaved(true);
            onConfigCreated?.();
            alert('Agent 配置已导入。');
        } catch (error: any) {
            console.error('Failed to import agent config:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
        } finally {
            setAgentSaving(false);
        }
    };

    const importModelConfigsFromItems = async (
        items: unknown[],
        existingConfigs: LLMConfig[],
        options?: { confirmDefault?: boolean }
    ) => {
        const usedNames = new Set(existingConfigs.map((item) => item.name));
        const existingByName = new Map(existingConfigs.map((item) => [item.name, item]));
        const payloads: Record<string, any>[] = [];
        const skipped: string[] = [];

        items.forEach((item, index) => {
            const { payload, error } = buildImportedConfigPayload(item, index, usedNames);
            if (payload) {
                payloads.push(payload);
            } else if (error) {
                skipped.push(error);
            }
        });

        if (payloads.length === 0) {
            return { success: 0, failed: [] as string[], skipped, cancelled: false };
        }

        let defaultFound = false;
        payloads.forEach((payload) => {
            if (payload.is_default && !defaultFound) {
                defaultFound = true;
            } else if (payload.is_default) {
                payload.is_default = false;
            }
        });

        const confirmDefault = options?.confirmDefault !== false;
        if (defaultFound && existingConfigs.length > 0 && confirmDefault) {
            const proceed = window.confirm('导入的配置包含默认项，导入后会切换默认配置。是否继续？');
            if (!proceed) {
                return { success: 0, failed: [] as string[], skipped, cancelled: true };
            }
        }

        let success = 0;
        const failed: string[] = [];
        for (const payload of payloads) {
            const name = String(payload.name || '').trim();
            if (!name) {
                failed.push('未命名: 缺少名称');
                continue;
            }
            const existing = existingByName.get(name);
            try {
                if (existing) {
                    const updatePayload: Record<string, any> = { ...payload };
                    if (!('api_key' in updatePayload)) {
                        delete updatePayload.api_key;
                    }
                    const updated = await updateConfig(existing.id, updatePayload);
                    existingByName.set(updated.name, updated);
                } else {
                    const createPayload: Record<string, any> = { ...payload };
                    if (!('api_key' in createPayload)) {
                        createPayload.api_key = '';
                    }
                    const created = await createConfig(createPayload as LLMConfigCreate);
                    existingByName.set(created.name, created);
                }
                success += 1;
            } catch (error: any) {
                failed.push(`${name}: ${error?.message || '导入失败'}`);
            }
        }

        return { success, failed, skipped, cancelled: false };
    };

    const extractAstBundleEntries = (value: unknown) => {
        const entries: { root: string; settings: Record<string, any> }[] = [];
        if (!value) return entries;
        if (Array.isArray(value)) {
            value.forEach((item) => {
                if (!isRecord(item)) return;
                const root = typeof item.root === 'string' ? item.root : '';
                const settings = isRecord(item.settings) ? item.settings : null;
                if (root && settings) {
                    entries.push({ root, settings });
                }
            });
            return entries;
        }
        if (!isRecord(value)) return entries;
        if (Array.isArray(value.paths)) {
            value.paths.forEach((item: unknown) => {
                if (!isRecord(item)) return;
                const root = typeof item.root === 'string' ? item.root : '';
                const settings = isRecord(item.settings) ? item.settings : null;
                if (root && settings) {
                    entries.push({ root, settings });
                }
            });
            return entries;
        }
        if (isRecord(value.paths)) {
            Object.entries(value.paths).forEach(([root, settings]) => {
                if (!root || !isRecord(settings)) return;
                entries.push({ root, settings });
            });
            return entries;
        }
        if (typeof value.root === 'string' && isRecord(value.settings)) {
            entries.push({ root: value.root, settings: value.settings });
        }
        return entries;
    };

    const buildAstPayload = (root: string, settings: Record<string, any>) => {
        const payload: Record<string, any> = { root };
        if ('ignore_paths' in settings) {
            payload.ignore_paths = normalizeAstImportList(settings.ignore_paths);
        }
        if ('include_only_paths' in settings) {
            payload.include_only_paths = normalizeAstImportList(settings.include_only_paths);
        }
        if ('force_include_paths' in settings) {
            payload.force_include_paths = normalizeAstImportList(settings.force_include_paths);
        }
        if ('include_languages' in settings) {
            payload.include_languages = normalizeAstImportLanguages(settings.include_languages);
        }
        if ('max_files' in settings) {
            const parsed = Number.parseInt(String(settings.max_files), 10);
            if (Number.isFinite(parsed) && parsed > 0) {
                payload.max_files = parsed;
            }
        }
        return payload;
    };

    const handleExportAllConfigs = async () => {
        setBundleBusy(true);
        try {
            const [latestConfigs, appConfig, astBundle] = await Promise.all([
                getConfigs(),
                getAppConfig(),
                getAstSettingsAll()
            ]);
            const payload = {
                models: { configs: latestConfigs },
                global: buildGlobalExportFromAppConfig(appConfig),
                agent: stripAgentGlobalFields(appConfig?.agent || {}),
                ast: { paths: Array.isArray(astBundle?.paths) ? astBundle.paths : [] }
            };
            const ok = await exportConfigFile('all', payload, {
                title: '导出全部配置',
                defaultName: buildExportName('all-configs'),
            });
            if (ok) alert('全部配置已导出。');
        } catch (error: any) {
            console.error('Failed to export all configs:', error);
            alert(`导出失败: ${error?.message || 'Unknown error'}`);
        } finally {
            setBundleBusy(false);
        }
    };

    const handleImportAllConfigs = async () => {
        let result;
        try {
            result = await importConfigFile({ title: '导入全部配置' });
        } catch (error: any) {
            console.error('Failed to import all configs:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
            return;
        }
        if (!result) return;
        if (!isAllowedKind(result.kind, ALL_IMPORT_KINDS)) {
            alert(`导入失败: 文件类型为 "${result.kind}"，不是完整配置包。`);
            return;
        }
        if (!isRecord(result.data)) {
            alert('未识别到有效的配置包。');
            return;
        }

        const data = result.data;
        const modelSource = isRecord(data.models)
            ? data.models
            : isRecord(data.llm_configs)
                ? data.llm_configs
                : data;
        const modelItems = extractConfigImportItems(modelSource);

        const globalPayload = extractGlobalImportPayload(
            isRecord(data.global)
                ? data.global
                : isRecord(data.app_config)
                    ? data.app_config
                    : isRecord(data.appConfig)
                        ? data.appConfig
                        : null
        );
        const agentPayload = extractAgentImportPayload(
            isRecord(data.agent)
                ? data.agent
                : isRecord(data.agent_config)
                    ? data.agent_config
                    : null
        );
        const astEntries = extractAstBundleEntries(
            isRecord(data.ast)
                ? data.ast
                : isRecord(data.ast_settings)
                    ? data.ast_settings
                    : isRecord(data.astSettings)
                        ? data.astSettings
                        : null
        );

        if (modelItems.length === 0 && !globalPayload && !agentPayload && astEntries.length === 0) {
            alert('配置包中没有可导入的内容。');
            return;
        }

        const summaryLines: string[] = [];
        if (modelItems.length > 0) summaryLines.push(`模型配置：${modelItems.length} 项`);
        if (globalPayload) summaryLines.push('全局配置');
        if (agentPayload) summaryLines.push('Agent 配置');
        if (astEntries.length > 0) summaryLines.push(`AST 设置：${astEntries.length} 个根路径`);

        const proceed = window.confirm(`将导入以下内容：\n${summaryLines.join('\n')}\n\n是否继续？`);
        if (!proceed) return;

        setBundleBusy(true);
        try {
            const latestConfigs = await getConfigs();
            let modelResult = { success: 0, failed: [] as string[], skipped: [] as string[], cancelled: false };
            if (modelItems.length > 0) {
                modelResult = await importModelConfigsFromItems(modelItems, latestConfigs, { confirmDefault: false });
            }

            const appPatch: AppConfigUpdate = {};
            if (globalPayload?.llm) appPatch.llm = globalPayload.llm;
            if (globalPayload?.context) appPatch.context = globalPayload.context;
            if (globalPayload?.agent || agentPayload) {
                const baseAgent = normalizeAgentConfig(agentConfig);
                let mergedAgent = baseAgent;
                if (agentPayload) {
                    mergedAgent = mergeAgentConfigByName(baseAgent, agentPayload);
                }
                if (globalPayload?.agent) {
                    mergedAgent = {
                        ...mergedAgent,
                        ...globalPayload.agent
                    };
                }
                appPatch.agent = mergedAgent;
            }

            let appUpdated = false;
            if (Object.keys(appPatch).length > 0) {
                const updated = await updateAppConfig(appPatch);
                applyAppConfigState(updated);
                appUpdated = true;
            }

            let astSuccess = 0;
            const astFailed: string[] = [];
            if (astEntries.length > 0) {
                for (const entry of astEntries) {
                    const payload = buildAstPayload(entry.root, entry.settings);
                    if (Object.keys(payload).length <= 1) {
                        continue;
                    }
                    try {
                        await updateAstSettings(payload as any);
                        astSuccess += 1;
                    } catch (error: any) {
                        astFailed.push(`${entry.root}: ${error?.message || '导入失败'}`);
                    }
                }
            }

            if (modelItems.length > 0) {
                await loadConfigs();
            }
            if (modelItems.length > 0 || appUpdated) {
                onConfigCreated?.();
            }

            const resultLines: string[] = [];
            if (modelItems.length > 0) {
                resultLines.push(`模型配置：成功 ${modelResult.success}，失败 ${modelResult.failed.length}，跳过 ${modelResult.skipped.length}`);
            }
            if (globalPayload) resultLines.push('全局配置：已更新');
            if (agentPayload) resultLines.push('Agent 配置：已更新');
            if (astEntries.length > 0) {
                resultLines.push(`AST 设置：成功 ${astSuccess}，失败 ${astFailed.length}`);
            }

            const detailLines = [
                ...modelResult.failed.map((item) => `模型失败: ${item}`),
                ...modelResult.skipped.map((item) => `模型跳过: ${item}`),
                ...astFailed.map((item) => `AST 失败: ${item}`)
            ];
            const detailText = detailLines.length ? `\n${detailLines.slice(0, 4).join('\n')}` : '';
            alert(`导入完成。\n${resultLines.join('\n')}${detailText}`);
        } catch (error: any) {
            console.error('Failed to import all configs:', error);
            alert(`导入失败: ${error?.message || 'Unknown error'}`);
        } finally {
            setBundleBusy(false);
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);

        try {
            if (editingConfig) {
                await updateConfig(editingConfig.id, formData);
            } else {
                await createConfig(formData);
            }
            await loadConfigs();
            resetForm();
            onConfigCreated?.();
        } catch (error: any) {
            console.error('Failed to save config:', error);
            let errorMessage = 'Failed to save config';
            if (error.message) {
                errorMessage += `: ${error.message}`;
            }
            if (error.message?.includes('fetch')) {
                errorMessage += '\n\nPlease verify:\n1) Backend is running\n2) http://127.0.0.1:8000 is reachable';
            }
            alert(errorMessage);
        } finally {
            setLoading(false);
        }
    };

    const handleGlobalSave = async (e: React.FormEvent) => {
        e.preventDefault();
        setGlobalSaving(true);
        setGlobalSaved(false);
        const timeoutValue = Number(globalTimeoutSec);
        if (!Number.isFinite(timeoutValue) || timeoutValue <= 0) {
            alert('Timeout must be a positive number of seconds.');
            setGlobalSaving(false);
            return;
        }
        const reactMaxValue = Number.parseInt(globalReactMaxIterations, 10);
        if (!Number.isFinite(reactMaxValue) || reactMaxValue < 1 || reactMaxValue > 200) {
            alert('ReAct max iterations must be an integer between 1 and 200.');
            setGlobalSaving(false);
            return;
        }
        const startPct = Number.parseInt(globalContextCompressStartPct, 10);
        if (!Number.isFinite(startPct) || startPct < 1 || startPct > 100) {
            alert('压缩触发阈值必须是 1 到 100 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        const targetPct = Number.parseInt(globalContextCompressTargetPct, 10);
        if (!Number.isFinite(targetPct) || targetPct < 1 || targetPct > 100) {
            alert('压缩目标必须是 1 到 100 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        if (targetPct >= startPct) {
            alert('压缩目标必须小于压缩触发阈值。');
            setGlobalSaving(false);
            return;
        }
        const minKeep = Number.parseInt(globalContextMinKeepMessages, 10);
        if (!Number.isFinite(minKeep) || minKeep < 0 || minKeep > 200) {
            alert('最少保留消息数必须是 0 到 200 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        const keepCalls = Number.parseInt(globalContextKeepRecentCalls, 10);
        if (!Number.isFinite(keepCalls) || keepCalls < 0 || keepCalls > 200) {
            alert('保留最近 Calls 必须是 0 到 200 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        const stepCalls = Number.parseInt(globalContextStepCalls, 10);
        if (!Number.isFinite(stepCalls) || stepCalls < 1 || stepCalls > 200) {
            alert('步进压缩 Calls 必须是 1 到 200 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        if (keepCalls > 0 && stepCalls > keepCalls) {
            alert('步进压缩 Calls 必须小于等于保留最近 Calls。');
            setGlobalSaving(false);
            return;
        }
        const longThreshold = Number.parseInt(globalContextLongThreshold, 10);
        if (!Number.isFinite(longThreshold) || longThreshold < 200 || longThreshold > 200000) {
            alert('长数据阈值必须是 200 到 200000 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        const longHead = Number.parseInt(globalContextLongHeadChars, 10);
        if (!Number.isFinite(longHead) || longHead < 0 || longHead > 200000) {
            alert('截断头部字符数必须是 0 到 200000 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        const longTail = Number.parseInt(globalContextLongTailChars, 10);
        if (!Number.isFinite(longTail) || longTail < 0 || longTail > 200000) {
            alert('截断尾部字符数必须是 0 到 200000 之间的整数。');
            setGlobalSaving(false);
            return;
        }
        if (longHead + longTail > longThreshold) {
            alert('截断头尾字符数之和必须小于等于长数据阈值。');
            setGlobalSaving(false);
            return;
        }
        try {
            const updated = await updateAppConfig({
                llm: { timeout_sec: timeoutValue },
                agent: {
                    react_max_iterations: reactMaxValue,
                    ast_enabled: globalAstEnabled,
                    code_map: {
                        enabled: globalCodeMapEnabled
                    }
                },
                context: {
                    compression_enabled: globalContextCompressionEnabled,
                    compress_start_pct: startPct,
                    compress_target_pct: targetPct,
                    min_keep_messages: minKeep,
                    keep_recent_calls: keepCalls,
                    step_calls: stepCalls,
                    truncate_long_data: globalContextTruncateLongData,
                    long_data_threshold: longThreshold,
                    long_data_head_chars: longHead,
                    long_data_tail_chars: longTail
                }
            });
            if (updated?.llm?.timeout_sec !== undefined && updated?.llm?.timeout_sec !== null) {
                setGlobalTimeoutSec(String(updated.llm.timeout_sec));
            }
            if (updated?.agent?.react_max_iterations !== undefined && updated?.agent?.react_max_iterations !== null) {
                setGlobalReactMaxIterations(String(updated.agent.react_max_iterations));
            }
            if (updated?.agent?.ast_enabled !== undefined && updated?.agent?.ast_enabled !== null) {
                setGlobalAstEnabled(Boolean(updated.agent.ast_enabled));
            }
            if (updated?.agent?.code_map?.enabled !== undefined && updated?.agent?.code_map?.enabled !== null) {
                setGlobalCodeMapEnabled(Boolean(updated.agent.code_map.enabled));
            }
            if (updated?.context) {
                setGlobalContextCompressionEnabled(Boolean(updated.context.compression_enabled));
                if (updated.context.compress_start_pct !== undefined && updated.context.compress_start_pct !== null) {
                    setGlobalContextCompressStartPct(String(updated.context.compress_start_pct));
                }
                if (updated.context.compress_target_pct !== undefined && updated.context.compress_target_pct !== null) {
                    setGlobalContextCompressTargetPct(String(updated.context.compress_target_pct));
                }
                if (updated.context.min_keep_messages !== undefined && updated.context.min_keep_messages !== null) {
                    setGlobalContextMinKeepMessages(String(updated.context.min_keep_messages));
                }
                if (updated.context.keep_recent_calls !== undefined && updated.context.keep_recent_calls !== null) {
                    setGlobalContextKeepRecentCalls(String(updated.context.keep_recent_calls));
                }
                if (updated.context.step_calls !== undefined && updated.context.step_calls !== null) {
                    setGlobalContextStepCalls(String(updated.context.step_calls));
                }
                if (updated.context.truncate_long_data !== undefined && updated.context.truncate_long_data !== null) {
                    setGlobalContextTruncateLongData(Boolean(updated.context.truncate_long_data));
                }
                if (updated.context.long_data_threshold !== undefined && updated.context.long_data_threshold !== null) {
                    setGlobalContextLongThreshold(String(updated.context.long_data_threshold));
                }
                if (updated.context.long_data_head_chars !== undefined && updated.context.long_data_head_chars !== null) {
                    setGlobalContextLongHeadChars(String(updated.context.long_data_head_chars));
                }
                if (updated.context.long_data_tail_chars !== undefined && updated.context.long_data_tail_chars !== null) {
                    setGlobalContextLongTailChars(String(updated.context.long_data_tail_chars));
                }
            }
            if (updated?.agent) {
                setAgentConfig(normalizeAgentConfig(updated.agent));
            }
            setGlobalSaved(true);
        } catch (error: any) {
            console.error('Failed to save app config:', error);
            let errorMessage = 'Failed to save global config';
            if (error.message) {
                errorMessage += `: ${error.message}`;
            }
            alert(errorMessage);
        } finally {
            setGlobalSaving(false);
        }
    };

    const makeId = (raw: string, existing: string[], fallbackPrefix: string) => {
        const base = raw
            .toLowerCase()
            .trim()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/(^-|-$)/g, '');
        let id = base || `${fallbackPrefix}-${Date.now()}`;
        let counter = 2;
        while (existing.includes(id)) {
            id = `${base || fallbackPrefix}-${counter}`;
            counter += 1;
        }
        return id;
    };

    const mergeAbilitiesByName = (
        current: AgentAbility[],
        incoming: AgentAbility[]
    ): { merged: AgentAbility[]; idMap: Map<string, string> } => {
        const merged = current.map((ability) => ({ ...ability }));
        const nameToIndex = new Map<string, number>();
        const idToIndex = new Map<string, number>();
        const existingIds: string[] = [];

        merged.forEach((ability, index) => {
            if (ability.id) {
                existingIds.push(ability.id);
                idToIndex.set(ability.id, index);
            }
            if (ability.name) {
                nameToIndex.set(ability.name.trim(), index);
            }
        });

        const idMap = new Map<string, string>();

        incoming.forEach((rawAbility, index) => {
            if (!rawAbility) return;
            const incomingAbility = rawAbility as AgentAbility;
            const rawName = typeof incomingAbility.name === 'string' ? incomingAbility.name.trim() : '';
            const rawId = typeof incomingAbility.id === 'string' ? incomingAbility.id.trim() : '';
            const nameKey = rawName || rawId || `ability-${index + 1}`;
            const existingIndex =
                (rawName && nameToIndex.has(rawName) ? nameToIndex.get(rawName) : undefined) ??
                (rawId && idToIndex.has(rawId) ? idToIndex.get(rawId) : undefined);

            if (existingIndex !== undefined) {
                const existing = merged[existingIndex];
                const finalId = existing.id || rawId || makeId(nameKey, existingIds, 'ability');
                if (rawId && rawId !== finalId) {
                    idMap.set(rawId, finalId);
                }
                if (!existing.id || existing.id !== finalId) {
                    existingIds.push(finalId);
                }
                const next: AgentAbility = {
                    ...existing,
                    ...incomingAbility,
                    id: finalId,
                    name: rawName || existing.name || nameKey,
                };
                if (!Array.isArray(incomingAbility.tools) && Array.isArray(existing.tools)) {
                    next.tools = existing.tools;
                }
                merged[existingIndex] = next;
                nameToIndex.set(next.name || nameKey, existingIndex);
                idToIndex.set(finalId, existingIndex);
                return;
            }

            let finalId = rawId || makeId(nameKey, existingIds, 'ability');
            if (existingIds.includes(finalId)) {
                finalId = makeId(nameKey, existingIds, 'ability');
            }
            existingIds.push(finalId);
            if (rawId && rawId !== finalId) {
                idMap.set(rawId, finalId);
            }

            const created: AgentAbility = {
                id: finalId,
                name: rawName || nameKey || finalId,
                type: incomingAbility.type || 'tooling',
                prompt: incomingAbility.prompt,
                tools: Array.isArray(incomingAbility.tools) ? incomingAbility.tools : [],
                params: incomingAbility.params,
            };
            merged.push(created);
            nameToIndex.set(created.name, merged.length - 1);
            idToIndex.set(finalId, merged.length - 1);
        });

        return { merged, idMap };
    };

    const mergeProfilesByName = (
        current: AgentProfile[],
        incoming: AgentProfile[],
        abilityIdMap: Map<string, string>
    ): { merged: AgentProfile[]; idMap: Map<string, string> } => {
        const merged = current.map((profile) => ({ ...profile }));
        const nameToIndex = new Map<string, number>();
        const idToIndex = new Map<string, number>();
        const existingIds: string[] = [];

        merged.forEach((profile, index) => {
            if (profile.id) {
                existingIds.push(profile.id);
                idToIndex.set(profile.id, index);
            }
            if (profile.name) {
                nameToIndex.set(profile.name.trim(), index);
            }
        });

        const idMap = new Map<string, string>();

        incoming.forEach((rawProfile, index) => {
            if (!rawProfile) return;
            const incomingProfile = rawProfile as AgentProfile;
            const rawName = typeof incomingProfile.name === 'string' ? incomingProfile.name.trim() : '';
            const rawId = typeof incomingProfile.id === 'string' ? incomingProfile.id.trim() : '';
            const nameKey = rawName || rawId || `profile-${index + 1}`;
            const existingIndex =
                (rawName && nameToIndex.has(rawName) ? nameToIndex.get(rawName) : undefined) ??
                (rawId && idToIndex.has(rawId) ? idToIndex.get(rawId) : undefined);
            const nextAbilities = Array.isArray(incomingProfile.abilities)
                ? incomingProfile.abilities.map((id) => abilityIdMap.get(id) || id)
                : null;

            if (existingIndex !== undefined) {
                const existing = merged[existingIndex];
                const finalId = existing.id || rawId || makeId(nameKey, existingIds, 'profile');
                if (rawId && rawId !== finalId) {
                    idMap.set(rawId, finalId);
                }
                if (!existing.id || existing.id !== finalId) {
                    existingIds.push(finalId);
                }
                const next: AgentProfile = {
                    ...existing,
                    ...incomingProfile,
                    id: finalId,
                    name: rawName || existing.name || nameKey,
                    abilities: nextAbilities ?? existing.abilities,
                };
                merged[existingIndex] = next;
                nameToIndex.set(next.name || nameKey, existingIndex);
                idToIndex.set(finalId, existingIndex);
                return;
            }

            let finalId = rawId || makeId(nameKey, existingIds, 'profile');
            if (existingIds.includes(finalId)) {
                finalId = makeId(nameKey, existingIds, 'profile');
            }
            existingIds.push(finalId);
            if (rawId && rawId !== finalId) {
                idMap.set(rawId, finalId);
            }
            const created: AgentProfile = {
                id: finalId,
                name: rawName || nameKey || finalId,
                abilities: nextAbilities ?? [],
                params: incomingProfile.params,
            };
            merged.push(created);
            nameToIndex.set(created.name, merged.length - 1);
            idToIndex.set(finalId, merged.length - 1);
        });

        return { merged, idMap };
    };

    const mergeAgentConfigByName = (current: AgentConfig, incoming: AgentConfig): AgentConfig => {
        const currentNormalized = normalizeAgentConfig(current);
        const incomingRaw = isRecord(incoming) ? incoming : {};
        const incomingAbilities = Array.isArray(incomingRaw.abilities) ? (incomingRaw.abilities as AgentAbility[]) : [];
        const incomingProfiles = Array.isArray(incomingRaw.profiles) ? (incomingRaw.profiles as AgentProfile[]) : [];

        const { merged: abilities, idMap: abilityIdMap } = mergeAbilitiesByName(
            Array.isArray(currentNormalized.abilities) ? currentNormalized.abilities : [],
            incomingAbilities
        );
        const { merged: profiles, idMap: profileIdMap } = mergeProfilesByName(
            Array.isArray(currentNormalized.profiles) ? currentNormalized.profiles : [],
            incomingProfiles,
            abilityIdMap
        );

        let defaultProfile = currentNormalized.default_profile || '';
        if (typeof incomingRaw.default_profile === 'string' && incomingRaw.default_profile.trim()) {
            const incomingDefault = incomingRaw.default_profile.trim();
            defaultProfile =
                profileIdMap.get(incomingDefault) ||
                profiles.find((profile) => profile.id === incomingDefault)?.id ||
                profiles.find((profile) => profile.name === incomingDefault)?.id ||
                defaultProfile;
        }

        return {
            ...currentNormalized,
            base_system_prompt: typeof incomingRaw.base_system_prompt === 'string'
                ? incomingRaw.base_system_prompt
                : currentNormalized.base_system_prompt,
            abilities,
            profiles,
            default_profile: defaultProfile,
        };
    };

    const handleAgentSave = async (e: React.FormEvent) => {
        e.preventDefault();
        setAgentSaving(true);
        setAgentSaved(false);
        try {
            const normalized = normalizeAgentConfig(agentConfig);
            const updated = await updateAppConfig({ agent: normalized });
            setAgentConfig(normalizeAgentConfig(updated?.agent));
            setAgentSaved(true);
            onConfigCreated?.();
        } catch (error: any) {
            console.error('Failed to save agent config:', error);
            let errorMessage = 'Failed to save agent config';
            if (error.message) {
                errorMessage += `: ${error.message}`;
            }
            alert(errorMessage);
        } finally {
            setAgentSaving(false);
        }
    };

    const openNewAbilityForm = () => {
        setEditingAbilityId(null);
        setAbilityForm({
            name: '',
            type: 'tooling',
            prompt: '',
            tools: [],
            paramsText: ''
        });
        setAbilityFormError(null);
        setShowAbilityForm(true);
    };

    const openEditAbilityForm = (ability: AgentAbility) => {
        setEditingAbilityId(ability.id);
        setAbilityForm({
            name: ability.name || '',
            type: ability.type || 'tooling',
            prompt: ability.prompt || '',
            tools: Array.isArray(ability.tools) ? ability.tools : [],
            paramsText: ability.params ? JSON.stringify(ability.params, null, 2) : ''
        });
        setAbilityFormError(null);
        setShowAbilityForm(true);
    };

    const handleSaveAbility = () => {
        const abilities = Array.isArray(agentConfig.abilities) ? [...agentConfig.abilities] : [];
        const existingIds = abilities.map((ability) => ability.id);
        let params: Record<string, any> | undefined;
        if (abilityForm.paramsText.trim()) {
            try {
                params = JSON.parse(abilityForm.paramsText);
            } catch (error: any) {
                setAbilityFormError('参数必须是合法的 JSON');
                return;
            }
        }

        const cleanedTools = abilityForm.tools.filter((tool) => typeof tool === 'string' && tool.trim());
        const normalizedTools = cleanedTools.includes('*') ? ['*'] : cleanedTools;

        if (editingAbilityId) {
            const index = abilities.findIndex((ability) => ability.id === editingAbilityId);
            if (index >= 0) {
                abilities[index] = {
                    ...abilities[index],
                    name: abilityForm.name.trim() || abilities[index].name,
                    type: abilityForm.type,
                    prompt: abilityForm.prompt,
                    tools: normalizedTools,
                    params
                };
            }
        } else {
            const id = makeId(abilityForm.name, existingIds, 'ability');
            abilities.push({
                id,
                name: abilityForm.name.trim() || id,
                type: abilityForm.type,
                prompt: abilityForm.prompt,
                tools: normalizedTools,
                params
            });
        }

        setAgentConfig((prev) => ({
            ...prev,
            abilities
        }));
        setAgentSaved(false);
        setShowAbilityForm(false);
    };

    const handleDeleteAbility = (abilityId: string) => {
        if (!window.confirm('确定要删除这个 ability 吗？')) return;
        const abilities = (agentConfig.abilities || []).filter((ability) => ability.id !== abilityId);
        const profiles = (agentConfig.profiles || []).map((profile) => ({
            ...profile,
            abilities: (profile.abilities || []).filter((id) => id !== abilityId)
        }));
        setAgentConfig((prev) => ({ ...prev, abilities, profiles }));
        setAgentSaved(false);
    };

    const openNewProfileForm = () => {
        setEditingProfileId(null);
        setProfileForm({
            name: '',
            abilities: [],
            paramsText: '',
            isDefault: false
        });
        setProfileFormError(null);
        setShowProfileForm(true);
    };

    const openEditProfileForm = (profile: AgentProfile) => {
        setEditingProfileId(profile.id);
        setProfileForm({
            name: profile.name || '',
            abilities: Array.isArray(profile.abilities) ? profile.abilities : [],
            paramsText: profile.params ? JSON.stringify(profile.params, null, 2) : '',
            isDefault: profile.id === agentConfig.default_profile
        });
        setProfileFormError(null);
        setShowProfileForm(true);
    };

    const handleSaveProfile = () => {
        const profiles = Array.isArray(agentConfig.profiles) ? [...agentConfig.profiles] : [];
        const existingIds = profiles.map((profile) => profile.id);
        let params: Record<string, any> | undefined;
        if (profileForm.paramsText.trim()) {
            try {
                params = JSON.parse(profileForm.paramsText);
            } catch (error: any) {
                setProfileFormError('参数必须是合法的 JSON');
                return;
            }
        }

        let profileId = editingProfileId;
        if (editingProfileId) {
            const index = profiles.findIndex((profile) => profile.id === editingProfileId);
            if (index >= 0) {
                profiles[index] = {
                    ...profiles[index],
                    name: profileForm.name.trim() || profiles[index].name,
                    abilities: profileForm.abilities,
                    params
                };
            }
        } else {
            profileId = makeId(profileForm.name, existingIds, 'profile');
            profiles.push({
                id: profileId,
                name: profileForm.name.trim() || profileId,
                abilities: profileForm.abilities,
                params
            });
        }

        let defaultProfile = agentConfig.default_profile || '';
        if (profileForm.isDefault && profileId) {
            defaultProfile = profileId;
        } else if (profileId && defaultProfile === profileId && !profileForm.isDefault) {
            defaultProfile = profiles.find((profile) => profile.id !== profileId)?.id || '';
        }

        setAgentConfig((prev) => ({
            ...prev,
            profiles,
            default_profile: defaultProfile
        }));
        setAgentSaved(false);
        setShowProfileForm(false);
    };

    const handleExportAgentPrompt = async () => {
        if (!agentConfig) return;
        const basePrompt = (agentConfig.base_system_prompt || '').trim();
        const profiles = Array.isArray(agentConfig.profiles) ? agentConfig.profiles : [];
        const profileId = agentConfig.default_profile || profiles[0]?.id || '';
        const profile = profiles.find((item) => item.id === profileId) || null;
        const abilityMap = new Map(
            (agentConfig.abilities || []).map((ability) => [ability.id, ability])
        );
        const abilityIds = profile?.abilities?.length
            ? profile.abilities
            : (agentConfig.abilities || []).map((ability) => ability.id);
        const promptAbilities = abilityIds
            .map((id) => abilityMap.get(id))
            .filter((ability): ability is AgentAbility => Boolean(ability))
            .filter((ability) => ability.id !== 'code_map' && ability.prompt && ability.prompt.trim());

        const lines: string[] = [];
        lines.push('# Agent Prompt');
        lines.push('');
        lines.push(`Exported: ${new Date().toLocaleString()}`);
        if (profile) {
            lines.push(`Profile: ${profile.name || profile.id}`);
        }
        lines.push('');
        lines.push('## Base System Prompt');
        lines.push('');
        lines.push('```');
        lines.push(basePrompt);
        lines.push('```');

        if (promptAbilities.length > 0) {
            lines.push('');
            lines.push('## Abilities');
            for (const ability of promptAbilities) {
                lines.push('');
                lines.push(`### ${ability.name || ability.id}`);
                lines.push('');
                lines.push('```');
                lines.push((ability.prompt || '').trim());
                lines.push('```');
            }
        }

        const defaultName = profile?.name
            ? `agent-prompt-${profile.name.replace(/[^a-z0-9-_]+/gi, '_')}.md`
            : 'agent-prompt.md';
        try {
            const target = await saveDialog({
                title: '导出提示词',
                defaultPath: defaultName,
                filters: [{ name: 'Markdown', extensions: ['md'] }]
            });
            if (!target) return;
            await writeTextFile(target, lines.join('\n'));
            alert('提示词已导出。');
        } catch (error: any) {
            console.error('Failed to export prompt:', error);
            alert(`导出失败: ${error?.message || 'Unknown error'}`);
        }
    };

    const handleDeleteProfile = (profileId: string) => {
        if (!window.confirm('确定要删除这个 profile 吗？')) return;
        const profiles = (agentConfig.profiles || []).filter((profile) => profile.id !== profileId);
        let defaultProfile = agentConfig.default_profile || '';
        if (defaultProfile === profileId) {
            defaultProfile = profiles[0]?.id || '';
        }
        setAgentConfig((prev) => ({
            ...prev,
            profiles,
            default_profile: defaultProfile
        }));
        setAgentSaved(false);
    };

    const handleEdit = (config: LLMConfig) => {
        setEditingConfig(config);
        setFormData({
            name: config.name,
            api_format: config.api_format,
            api_profile: config.api_profile,
            api_key: config.api_key,
            base_url: config.base_url || '',
            model: config.model,
            temperature: config.temperature,
            max_tokens: config.max_tokens,
            max_context_tokens: config.max_context_tokens ?? 200000,
            is_default: config.is_default,
        });
        setShowForm(true);
    };

    const getCopyName = (baseName: string) => {
        const existingNames = new Set(configs.map((item) => item.name));
        const baseCopy = `${baseName} (copy)`;
        if (!existingNames.has(baseCopy)) {
            return baseCopy;
        }
        let index = 2;
        while (existingNames.has(`${baseName} (copy ${index})`)) {
            index += 1;
        }
        return `${baseName} (copy ${index})`;
    };

    const handleCopy = async (config: LLMConfig) => {
        const copyData: LLMConfigCreate = {
            name: getCopyName(config.name),
            api_format: config.api_format,
            api_profile: config.api_profile,
            api_key: config.api_key,
            base_url: config.base_url || '',
            model: config.model,
            temperature: config.temperature,
            max_tokens: config.max_tokens,
            max_context_tokens: config.max_context_tokens ?? 200000,
            is_default: false,
            reasoning_effort: config.reasoning_effort,
            reasoning_summary: config.reasoning_summary,
        };
        try {
            await createConfig(copyData);
            await loadConfigs();
            onConfigCreated?.();
        } catch (error: any) {
            console.error('Failed to copy config:', error);
            let errorMessage = 'Failed to copy config';
            if (error.message) {
                errorMessage += `: ${error.message}`;
            }
            if (error.message?.includes('fetch')) {
                errorMessage += '\n\nPlease verify:\n1) Backend is running\n2) http://127.0.0.1:8000 is reachable';
            }
            alert(errorMessage);
        }
    };

    const handleDelete = (config: LLMConfig) => {
        setDeleteTarget(config);
    };

    const handleConfirmDelete = async () => {
        if (!deleteTarget) return;
        try {
            await deleteConfig(deleteTarget.id);
            await loadConfigs();
            onConfigCreated?.();
        } catch (error: any) {
            console.error('Failed to delete config:', error);
            alert(error.message || 'Failed to delete config.');
        } finally {
            setDeleteTarget(null);
        }
    };

    const resetForm = () => {
        setFormData({
            name: '',
            api_format: 'openai_chat_completions',
            api_profile: 'openai',
            api_key: '',
            base_url: '',
            model: '',
            temperature: 0.7,
            max_tokens: 2000,
            max_context_tokens: 200000,
            is_default: false,
        });
        setEditingConfig(null);
        setShowForm(false);
    };

    const getModelPlaceholder = () => {
        switch (formData.api_profile) {
            case 'openai':
                return 'gpt-4o, gpt-4.1, gpt-3.5-turbo';
            case 'deepseek':
                return 'deepseek-chat, deepseek-reasoner';
            case 'zhipu':
                return 'glm-4, glm-3-turbo';
            case 'openai_compatible':
            default:
                return 'model-id';
        }
    };

    const getBaseUrlPlaceholder = () => {
        switch (formData.api_profile) {
            case 'openai':
                return 'https://api.openai.com/v1';
            case 'deepseek':
                return 'https://api.deepseek.com/v1';
            case 'zhipu':
                return 'https://open.bigmodel.cn/api/paas/v4';
            case 'openai_compatible':
            default:
                return 'Provider base URL';
        }
    };

    const handleFormatChange = (value: LLMApiFormat) => {
        const next: LLMConfigCreate = { ...formData, api_format: value };
        if (value === 'openai_responses' && formData.api_profile !== 'openai' && formData.api_profile !== 'openai_compatible') {
            next.api_profile = 'openai';
        }
        setFormData(next);
    };

    const handleProfileChange = (value: LLMProfile) => {
        if (formData.api_format === 'openai_responses' && value !== 'openai' && value !== 'openai_compatible') {
            setFormData({ ...formData, api_profile: 'openai' });
            return;
        }
        setFormData({ ...formData, api_profile: value });
    };

    const allToolsSelected = abilityForm.tools.includes('*');
    const bundleDisabled = bundleBusy || globalLoading || globalSaving || agentLoading || agentSaving || loading;

    return (
        <div className="modal-overlay">
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div className="modal-header-title">
                        <h2>配置管理</h2>
                        <div className="config-header-actions">
                            <button
                                type="button"
                                className="add-btn add-inline"
                                onClick={handleImportAllConfigs}
                                disabled={bundleDisabled}
                            >
                                导入全部
                            </button>
                            <button
                                type="button"
                                className="add-btn add-inline"
                                onClick={handleExportAllConfigs}
                                disabled={bundleDisabled}
                            >
                                导出全部
                            </button>
                        </div>
                    </div>
                    <button className="close-btn" onClick={onClose}>X</button>
                </div>

                <div className="modal-body">
                    <div className="config-tabs">
                        <button
                            className={`config-tab ${activeTab === 'models' ? 'active' : ''}`}
                            onClick={() => setActiveTab('models')}
                            type="button"
                        >
                            模型配置
                        </button>
                        <button
                            className={`config-tab ${activeTab === 'global' ? 'active' : ''}`}
                            onClick={() => {
                                setActiveTab('global');
                                setGlobalSaved(false);
                            }}
                            type="button"
                        >
                            全局配置
                        </button>
                        <button
                            className={`config-tab ${activeTab === 'agents' ? 'active' : ''}`}
                            onClick={() => {
                                setActiveTab('agents');
                                setAgentSaved(false);
                            }}
                            type="button"
                        >
                            Agent配置
                        </button>
                    </div>

                    {activeTab === 'models' ? (
                        <>
                            <div className="config-toolbar">
                                <button className="add-btn add-inline" onClick={() => setShowForm(true)} type="button">
                                    + Add Config
                                </button>
                                <div className="config-toolbar-actions">
                                    <button
                                        className="add-btn add-inline"
                                        type="button"
                                        onClick={handleImportConfigs}
                                    >
                                        导入
                                    </button>
                                    <button
                                        className="add-btn add-inline"
                                        type="button"
                                        onClick={handleExportConfigs}
                                    >
                                        导出
                                    </button>
                                </div>
                            </div>

                            <div className="configs-list">
                                {configs.length === 0 ? (
                                    <p className="empty-message">No configs yet.</p>
                                ) : (
                                    configs.map((config) => (
                                        <div key={config.id} className="config-item">
                                            <div className="config-info">
                                                <h3>
                                                    {config.name}
                                                    {config.is_default && <span className="badge">Default</span>}
                                                </h3>
                                                <p className="config-detail">
                                                    <strong>Format:</strong> {config.api_format} |
                                                    <strong> Profile:</strong> {config.api_profile}
                                                </p>
                                                <p className="config-detail">
                                                    <strong>Model:</strong> {config.model}
                                                </p>
                                                <p className="config-detail">
                                                    <strong>Temp:</strong> {config.temperature} |
                                                    <strong> Max Tokens:</strong> {config.max_tokens} |
                                                    <strong> Max Context:</strong> {config.max_context_tokens ?? 200000}
                                                </p>
                                            </div>
                                            <div className="config-actions">
                                                <button className="icon-btn" onClick={() => handleEdit(config)} aria-label="Edit" title="Edit">
                                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                                        <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z" />
                                                        <path d="M20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                                                    </svg>
                                                </button>
                                                <button className="icon-btn" onClick={() => handleCopy(config)} aria-label="Copy" title="Copy">
                                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                                        <path d="M16 1H4a2 2 0 00-2 2v12h2V3h12V1z" />
                                                        <path d="M20 5H8a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V7a2 2 0 00-2-2zm0 16H8V7h12v14z" />
                                                    </svg>
                                                </button>
                                                <button className="icon-btn delete-btn" onClick={() => handleDelete(config)} aria-label="Delete" title="Delete">
                                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                                        <path d="M6 7h12l-1 14H7L6 7z" />
                                                        <path d="M9 4h6l1 2H8l1-2z" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </>
                    ) : activeTab === 'global' ? (
                        <form onSubmit={handleGlobalSave} className="config-form">
                            <div className="config-form-header">
                                <h3>全局配置</h3>
                                <div className="config-form-actions">
                                    <button
                                        type="button"
                                        className="add-btn add-inline"
                                        onClick={handleImportGlobalConfig}
                                        disabled={globalLoading || globalSaving}
                                    >
                                        导入
                                    </button>
                                    <button
                                        type="button"
                                        className="add-btn add-inline"
                                        onClick={handleExportGlobalConfig}
                                        disabled={globalLoading || globalSaving}
                                    >
                                        导出
                                    </button>
                                </div>
                            </div>

                            <div className="form-group">
                                <label>LLM 超时（秒）</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="3600"
                                    step="1"
                                    value={globalTimeoutSec}
                                    onChange={(e) => {
                                        setGlobalTimeoutSec(e.target.value);
                                        setGlobalSaved(false);
                                    }}
                                    disabled={globalLoading || globalSaving}
                                />
                                <small>应用于所有模型请求。默认 180 秒。</small>
                            </div>

                            <div className="form-group">
                                <label>ReAct 最大迭代次数</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="200"
                                    step="1"
                                    value={globalReactMaxIterations}
                                    onChange={(e) => {
                                        setGlobalReactMaxIterations(e.target.value);
                                        setGlobalSaved(false);
                                    }}
                                    disabled={globalLoading || globalSaving}
                                />
                                <small>ReAct agent 每次对话允许的最大循环步数（1-200）。</small>
                            </div>

                            <div className="config-subsection">
                                <div className="config-subsection-header">
                                    <h4>代码分析</h4>
                                    <span>控制 AST 与 Code Map 生成。</span>
                                </div>

                                <div className="form-group checkbox-group">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={globalAstEnabled}
                                            onChange={(e) => {
                                                setGlobalAstEnabled(e.target.checked);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        启用 AST 解析
                                    </label>
                                </div>

                                <div className="form-group checkbox-group">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={globalCodeMapEnabled}
                                            onChange={(e) => {
                                                setGlobalCodeMapEnabled(e.target.checked);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        启用 Code Map
                                    </label>
                                </div>
                            </div>

                            <div className="config-subsection">
                                <div className="config-subsection-header">
                                    <h4>Context 管理</h4>
                                    <span>仅保存参数，压缩逻辑稍后接入。</span>
                                </div>

                                <div className="form-group checkbox-group">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={globalContextCompressionEnabled}
                                            onChange={(e) => {
                                                setGlobalContextCompressionEnabled(e.target.checked);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        启用压缩策略
                                    </label>
                                </div>

                                <div className="form-row">
                                    <div className="form-group">
                                        <label>压缩触发阈值 (%)</label>
                                        <input
                                            type="number"
                                            min="1"
                                            max="100"
                                            step="1"
                                            value={globalContextCompressStartPct}
                                            onChange={(e) => {
                                                setGlobalContextCompressStartPct(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>当 context 使用率达到此百分比时触发压缩。</small>
                                    </div>
                                    <div className="form-group">
                                        <label>压缩目标 (%)</label>
                                        <input
                                            type="number"
                                            min="1"
                                            max="100"
                                            step="1"
                                            value={globalContextCompressTargetPct}
                                            onChange={(e) => {
                                                setGlobalContextCompressTargetPct(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>压缩后期望降到的占用比例。</small>
                                    </div>
                                </div>

                                <div className="form-group">
                                    <label>最少保留消息数</label>
                                    <input
                                        type="number"
                                        min="0"
                                        max="200"
                                        step="1"
                                        value={globalContextMinKeepMessages}
                                        onChange={(e) => {
                                            setGlobalContextMinKeepMessages(e.target.value);
                                            setGlobalSaved(false);
                                        }}
                                        disabled={globalLoading || globalSaving}
                                    />
                                    <small>压缩时至少保留最近 N 条消息。</small>
                                </div>

                                <div className="form-row">
                                    <div className="form-group">
                                        <label>保留最近 Calls</label>
                                        <input
                                            type="number"
                                            min="0"
                                            max="200"
                                            step="1"
                                            value={globalContextKeepRecentCalls}
                                            onChange={(e) => {
                                                setGlobalContextKeepRecentCalls(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>压缩时保留最近的 LLM Call 数量。</small>
                                    </div>
                                    <div className="form-group">
                                        <label>步进压缩 Calls</label>
                                        <input
                                            type="number"
                                            min="1"
                                            max="200"
                                            step="1"
                                            value={globalContextStepCalls}
                                            onChange={(e) => {
                                                setGlobalContextStepCalls(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>超标后每次缩减的 Call 数量。</small>
                                    </div>
                                </div>

                                <div className="form-group checkbox-group">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={globalContextTruncateLongData}
                                            onChange={(e) => {
                                                setGlobalContextTruncateLongData(e.target.checked);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        启用长数据截断
                                    </label>
                                </div>

                                <div className="form-row">
                                    <div className="form-group">
                                        <label>长数据阈值 (chars)</label>
                                        <input
                                            type="number"
                                            min="200"
                                            max="200000"
                                            step="1"
                                            value={globalContextLongThreshold}
                                            onChange={(e) => {
                                                setGlobalContextLongThreshold(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>超过此长度的参数或输出会被截断。</small>
                                    </div>
                                    <div className="form-group">
                                        <label>保留头部字符数</label>
                                        <input
                                            type="number"
                                            min="0"
                                            max="200000"
                                            step="1"
                                            value={globalContextLongHeadChars}
                                            onChange={(e) => {
                                                setGlobalContextLongHeadChars(e.target.value);
                                                setGlobalSaved(false);
                                            }}
                                            disabled={globalLoading || globalSaving}
                                        />
                                        <small>截断时保留的头部长度。</small>
                                    </div>
                                </div>

                                <div className="form-group">
                                    <label>保留尾部字符数</label>
                                    <input
                                        type="number"
                                        min="0"
                                        max="200000"
                                        step="1"
                                        value={globalContextLongTailChars}
                                        onChange={(e) => {
                                            setGlobalContextLongTailChars(e.target.value);
                                            setGlobalSaved(false);
                                        }}
                                        disabled={globalLoading || globalSaving}
                                    />
                                    <small>截断时保留的尾部长度。</small>
                                </div>
                            </div>

                            <div className="form-actions">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setGlobalSaved(false);
                                        loadAppConfig();
                                    }}
                                    disabled={globalLoading || globalSaving}
                                >
                                    重置
                                </button>
                                <button type="submit" disabled={globalLoading || globalSaving}>
                                    {globalSaving ? 'Saving...' : 'Save'}
                                </button>
                            </div>
                            {globalSaved && <div className="save-hint">已保存</div>}
                        </form>
                    ) : (
                        <form onSubmit={handleAgentSave} className="config-form">
                            <div className="config-form-header">
                                <h3>Agent配置</h3>
                                <div className="config-form-actions">
                                    <button
                                        type="button"
                                        className="add-btn add-inline"
                                        onClick={handleImportAgentConfig}
                                        disabled={agentLoading || agentSaving}
                                    >
                                        导入
                                    </button>
                                    <button
                                        type="button"
                                        className="add-btn add-inline"
                                        onClick={handleExportAgentConfig}
                                        disabled={agentLoading || agentSaving}
                                    >
                                        导出
                                    </button>
                                </div>
                            </div>

                            <div className="form-group">
                                <label>基础系统提示词</label>
                                <textarea
                                    rows={4}
                                    value={agentConfig.base_system_prompt || ''}
                                    onChange={(e) => {
                                        setAgentConfig((prev) => ({
                                            ...prev,
                                            base_system_prompt: e.target.value
                                        }));
                                        setAgentSaved(false);
                                    }}
                                />
                                <small>用于所有 agent 的基础系统提示词。</small>
                            </div>

                            <div className="agent-section">
                                <div className="agent-section-header">
                                    <h4>Abilities</h4>
                                    <button type="button" className="add-btn add-inline" onClick={openNewAbilityForm}>
                                        + Add Ability
                                    </button>
                                </div>
                                <div className="agent-list">
                                    {(agentConfig.abilities || []).length === 0 ? (
                                        <p className="empty-message">No abilities yet.</p>
                                    ) : (
                                        (agentConfig.abilities || []).map((ability) => (
                                            <div key={ability.id} className="agent-item">
                                                <div className="agent-info">
                                                    <h4>{ability.name}</h4>
                                                    <p className="agent-detail">
                                                        <strong>Type:</strong> {ability.type || 'misc'}{' '}
                                                        <strong>Tools:</strong>{' '}
                                                        {Array.isArray(ability.tools) && ability.tools.length > 0
                                                            ? ability.tools.join(', ')
                                                            : '-'}
                                                    </p>
                                                </div>
                                                <div className="config-actions">
                                                    <button
                                                        className="icon-btn"
                                                        onClick={() => openEditAbilityForm(ability)}
                                                        type="button"
                                                        aria-label="Edit ability"
                                                        title="Edit"
                                                    >
                                                        <svg viewBox="0 0 24 24" aria-hidden="true">
                                                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z" />
                                                            <path d="M20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        className="icon-btn delete-btn"
                                                        onClick={() => handleDeleteAbility(ability.id)}
                                                        type="button"
                                                        aria-label="Delete ability"
                                                        title="Delete"
                                                    >
                                                        <svg viewBox="0 0 24 24" aria-hidden="true">
                                                            <path d="M6 7h12l-1 14H7L6 7z" />
                                                            <path d="M9 4h6l1 2H8l1-2z" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </div>

                            <div className="agent-section">
                                <div className="agent-section-header">
                                    <h4>Profiles</h4>
                                    <div className="agent-section-actions">
                                        <button type="button" className="add-btn add-inline" onClick={handleExportAgentPrompt}>
                                            导出提示词
                                        </button>
                                        <button type="button" className="add-btn add-inline" onClick={openNewProfileForm}>
                                            + Add Profile
                                        </button>
                                    </div>
                                </div>
                                {showProfileForm && (
                                    <div className="inline-modal">
                                        <div className="modal-header">
                                            <h2>{editingProfileId ? 'Edit Profile' : 'Create Profile'}</h2>
                                            <button
                                                type="button"
                                                className="close-btn"
                                                onClick={() => setShowProfileForm(false)}
                                            >
                                                X
                                            </button>
                                        </div>
                                        <div className="modal-body">
                                            <div className="config-form">
                                                <div className="form-group">
                                                    <label>Name *</label>
                                                    <input
                                                        type="text"
                                                        value={profileForm.name}
                                                        onChange={(e) =>
                                                            setProfileForm({ ...profileForm, name: e.target.value })
                                                        }
                                                        placeholder="Profile name"
                                                        required
                                                    />
                                                </div>

                                                <div className="form-group">
                                                    <label>Abilities</label>
                                                    <div className="checkbox-grid">
                                                        {(agentConfig.abilities || []).map((ability) => {
                                                            const checked = profileForm.abilities.includes(ability.id);
                                                            return (
                                                                <label key={ability.id} className="checkbox-inline">
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={checked}
                                                                        onChange={(e) => {
                                                                            const next = new Set(profileForm.abilities);
                                                                            if (e.target.checked) {
                                                                                next.add(ability.id);
                                                                            } else {
                                                                                next.delete(ability.id);
                                                                            }
                                                                            setProfileForm({
                                                                                ...profileForm,
                                                                                abilities: Array.from(next)
                                                                            });
                                                                        }}
                                                                    />
                                                                    {ability.name}
                                                                </label>
                                                            );
                                                        })}
                                                    </div>
                                                </div>

                                                <div className="form-group">
                                                    <label>Params (JSON)</label>
                                                    <textarea
                                                        rows={4}
                                                        value={profileForm.paramsText}
                                                        onChange={(e) =>
                                                            setProfileForm({ ...profileForm, paramsText: e.target.value })
                                                        }
                                                        placeholder='{"key": "value"}'
                                                    />
                                                </div>

                                                <div className="form-group checkbox-group">
                                                    <label>
                                                        <input
                                                            type="checkbox"
                                                            checked={profileForm.isDefault}
                                                            onChange={(e) =>
                                                                setProfileForm({
                                                                    ...profileForm,
                                                                    isDefault: e.target.checked
                                                                })
                                                            }
                                                        />
                                                        Set as default
                                                    </label>
                                                </div>

                                                {profileFormError && <div className="form-error">{profileFormError}</div>}

                                                <div className="form-actions">
                                                    <button type="button" onClick={() => setShowProfileForm(false)}>
                                                        Cancel
                                                    </button>
                                                    <button type="button" onClick={handleSaveProfile}>
                                                        Save
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}
                                <div className="agent-list">
                                    {(agentConfig.profiles || []).length === 0 ? (
                                        <p className="empty-message">No profiles yet.</p>
                                    ) : (
                                        (agentConfig.profiles || []).map((profile) => (
                                            <div key={profile.id} className="agent-item">
                                                <div className="agent-info">
                                                    <h4>
                                                        {profile.name}
                                                        {profile.id === agentConfig.default_profile && (
                                                            <span className="badge">Default</span>
                                                        )}
                                                    </h4>
                                                    <p className="agent-detail">
                                                        <strong>Abilities:</strong>{' '}
                                                        {(profile.abilities || []).length > 0
                                                            ? profile.abilities
                                                                .map((abilityId) => {
                                                                    const ability = (agentConfig.abilities || []).find(
                                                                        (item) => item.id === abilityId
                                                                    );
                                                                    return ability?.name || abilityId;
                                                                })
                                                                .join(', ')
                                                            : '-'}
                                                    </p>
                                                </div>
                                                <div className="config-actions">
                                                    <button
                                                        className="icon-btn"
                                                        onClick={() => openEditProfileForm(profile)}
                                                        type="button"
                                                        aria-label="Edit profile"
                                                        title="Edit"
                                                    >
                                                        <svg viewBox="0 0 24 24" aria-hidden="true">
                                                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z" />
                                                            <path d="M20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        className="icon-btn delete-btn"
                                                        onClick={() => handleDeleteProfile(profile.id)}
                                                        type="button"
                                                        aria-label="Delete profile"
                                                        title="Delete"
                                                    >
                                                        <svg viewBox="0 0 24 24" aria-hidden="true">
                                                            <path d="M6 7h12l-1 14H7L6 7z" />
                                                            <path d="M9 4h6l1 2H8l1-2z" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </div>

                            <div className="form-actions">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setAgentSaved(false);
                                        setAgentLoading(true);
                                        loadAppConfig().finally(() => setAgentLoading(false));
                                    }}
                                    disabled={agentLoading || agentSaving}
                                >
                                    重置
                                </button>
                                <button type="submit" disabled={agentLoading || agentSaving}>
                                    {agentSaving ? 'Saving...' : 'Save'}
                                </button>
                            </div>
                            {agentSaved && <div className="save-hint">已保存</div>}
                        </form>
                    )}
                </div>
            </div>

            {showForm && activeTab === 'models' && (
                <div className="modal-overlay modal-overlay-nested" onClick={resetForm}>
                    <div className="modal-content modal-content-nested" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <h2>{editingConfig ? 'Edit Config' : 'Create Config'}</h2>
                            <button type="button" className="close-btn" onClick={resetForm}>X</button>
                        </div>
                        <div className="modal-body">
                            <form onSubmit={handleSubmit} className="config-form">

                                <div className="form-group">
                                    <label>Name *</label>
                                    <input
                                        type="text"
                                        value={formData.name}
                                        onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                        placeholder="Example: OpenAI GPT-4"
                                        required
                                    />
                                </div>

                                <div className="form-group">
                                    <label>Format *</label>
                                    <select
                                        value={formData.api_format}
                                        onChange={(e) => handleFormatChange(e.target.value as LLMApiFormat)}
                                        required
                                    >
                                        {FORMAT_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>{option.label}</option>
                                        ))}
                                    </select>
                                </div>

                                <div className="form-group">
                                    <label>Profile *</label>
                                    <select
                                        value={formData.api_profile}
                                        onChange={(e) => handleProfileChange(e.target.value as LLMProfile)}
                                        required
                                    >
                                        {PROFILE_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>{option.label}</option>
                                        ))}
                                    </select>
                                    {formData.api_format === 'openai_responses' && formData.api_profile !== 'openai' && (
                                        <small>Responses format requires an OpenAI-compatible profile.</small>
                                    )}
                                </div>

                                <div className="form-group">
                                    <label>API Key *</label>
                                    <input
                                        type="password"
                                        value={formData.api_key}
                                        onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                                        placeholder="Enter API Key"
                                        required
                                    />
                                </div>

                                <div className="form-group">
                                    <label>Base URL (Optional)</label>
                                    <input
                                        type="text"
                                        value={formData.base_url}
                                        onChange={(e) => setFormData({ ...formData, base_url: e.target.value })}
                                        placeholder={getBaseUrlPlaceholder()}
                                    />
                                    <small>Leave empty to use default base URL.</small>
                                </div>

                                <div className="form-group">
                                    <label>Model *</label>
                                    <input
                                        type="text"
                                        value={formData.model}
                                        onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                                        placeholder={getModelPlaceholder()}
                                        required
                                    />
                                </div>

                                <div className="form-row">
                                    <div className="form-group">
                                        <label>Temperature (0-2)</label>
                                        <input
                                            type="number"
                                            step="0.1"
                                            min="0"
                                            max="2"
                                            value={formData.temperature}
                                            onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                                        />
                                    </div>

                                    <div className="form-group">
                                        <label>Max Tokens</label>
                                        <input
                                            type="number"
                                            min="1"
                                            max="32000"
                                            value={formData.max_tokens}
                                            onChange={(e) => setFormData({ ...formData, max_tokens: parseInt(e.target.value) })}
                                        />
                                    </div>
                                </div>

                                <div className="form-group">
                                    <label>Max Context Tokens</label>
                                    <input
                                        type="number"
                                        min="1000"
                                        max="1000000"
                                        value={formData.max_context_tokens ?? 200000}
                                        onChange={(e) =>
                                            setFormData({ ...formData, max_context_tokens: parseInt(e.target.value) })
                                        }
                                    />
                                    <small>默认 200000。用于 context 使用率估算。</small>
                                </div>

                                <div className="form-group checkbox-group">
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={formData.is_default}
                                            onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
                                        />
                                        Set as default
                                    </label>
                                </div>

                                <div className="form-actions">
                                    <button type="button" onClick={resetForm} disabled={loading}>
                                        Cancel
                                    </button>
                                    <button type="submit" disabled={loading}>
                                        {loading ? 'Saving...' : 'Save'}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            )}

            {showAbilityForm && activeTab === 'agents' && (
                <div className="modal-overlay modal-overlay-nested" onClick={() => setShowAbilityForm(false)}>
                    <div className="modal-content modal-content-nested" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <h2>{editingAbilityId ? 'Edit Ability' : 'Create Ability'}</h2>
                            <button
                                type="button"
                                className="close-btn"
                                onClick={() => setShowAbilityForm(false)}
                            >
                                X
                            </button>
                        </div>
                        <div className="modal-body">
                            <div className="config-form">
                                <div className="form-group">
                                    <label>Name *</label>
                                    <input
                                        type="text"
                                        value={abilityForm.name}
                                        onChange={(e) =>
                                            setAbilityForm({ ...abilityForm, name: e.target.value })
                                        }
                                        placeholder="Ability name"
                                        required
                                    />
                                </div>

                                <div className="form-group">
                                    <label>Type *</label>
                                    <select
                                        value={abilityForm.type}
                                        onChange={(e) =>
                                            setAbilityForm({ ...abilityForm, type: e.target.value })
                                        }
                                    >
                                        {ABILITY_TYPE_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>
                                                {option.label}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="form-group">
                                    <label>Tools</label>
                                    <div className="checkbox-grid">
                                        <label className="checkbox-inline">
                                            <input
                                                type="checkbox"
                                                checked={allToolsSelected}
                                                onChange={(e) => {
                                                    if (e.target.checked) {
                                                        setAbilityForm({ ...abilityForm, tools: ['*'] });
                                                    } else {
                                                        setAbilityForm({ ...abilityForm, tools: [] });
                                                    }
                                                }}
                                            />
                                            All tools
                                        </label>
                                        {availableTools.map((tool) => {
                                            const checked = abilityForm.tools.includes(tool.name);
                                            return (
                                                <label key={tool.name} className="checkbox-inline">
                                                    <input
                                                        type="checkbox"
                                                        checked={checked}
                                                        disabled={allToolsSelected}
                                                        onChange={(e) => {
                                                            const next = new Set(
                                                                abilityForm.tools.filter((name) => name !== '*')
                                                            );
                                                            if (e.target.checked) {
                                                                next.add(tool.name);
                                                            } else {
                                                                next.delete(tool.name);
                                                            }
                                                            setAbilityForm({
                                                                ...abilityForm,
                                                                tools: Array.from(next)
                                                            });
                                                        }}
                                                    />
                                                    {tool.name}
                                                </label>
                                            );
                                        })}
                                    </div>
                                </div>

                                <div className="form-group">
                                    <label>Prompt</label>
                                    <textarea
                                        rows={6}
                                        value={abilityForm.prompt}
                                        onChange={(e) =>
                                            setAbilityForm({ ...abilityForm, prompt: e.target.value })
                                        }
                                        placeholder="Prompt text (optional)"
                                    />
                                </div>

                                <div className="form-group">
                                    <label>Params (JSON)</label>
                                    <textarea
                                        rows={4}
                                        value={abilityForm.paramsText}
                                        onChange={(e) =>
                                            setAbilityForm({ ...abilityForm, paramsText: e.target.value })
                                        }
                                        placeholder='{"key": "value"}'
                                    />
                                    <small>可选。支持 {'{{param}}'} 占位符。</small>
                                </div>

                                {abilityFormError && <div className="form-error">{abilityFormError}</div>}

                                <div className="form-actions">
                                    <button type="button" onClick={() => setShowAbilityForm(false)}>
                                        Cancel
                                    </button>
                                    <button type="button" onClick={handleSaveAbility}>
                                        Save
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
            <ConfirmDialog
                open={Boolean(deleteTarget)}
                title="Delete config"
                message={`Delete config "${deleteTarget?.name || ''}"? This cannot be undone.`}
                confirmLabel="Delete"
                cancelLabel="Cancel"
                danger
                onCancel={() => setDeleteTarget(null)}
                onConfirm={handleConfirmDelete}
            />
        </div>
    );
}
