import { useState, useEffect } from 'react';
import {
    LLMConfig,
    LLMConfigCreate,
    LLMApiFormat,
    LLMProfile,
    AgentConfig,
    AgentAbility,
    AgentProfile,
    ToolDefinition
} from '../types';
import { getConfigs, createConfig, updateConfig, deleteConfig, getAppConfig, updateAppConfig, getTools } from '../api';
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


export default function ConfigManager({ onClose, onConfigCreated }: ConfigManagerProps) {
    type ConfigTab = 'models' | 'global' | 'agents';
    const [configs, setConfigs] = useState<LLMConfig[]>([]);
    const [activeTab, setActiveTab] = useState<ConfigTab>('models');
    const [globalTimeoutSec, setGlobalTimeoutSec] = useState('180');
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

    const normalizeAgentConfig = (data?: AgentConfig | null): AgentConfig => ({
        base_system_prompt: data?.base_system_prompt ?? '',
        abilities: Array.isArray(data?.abilities) ? data!.abilities : [],
        profiles: Array.isArray(data?.profiles) ? data!.profiles : [],
        default_profile: data?.default_profile ?? '',
    });

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

    const loadAppConfig = async () => {
        setGlobalLoading(true);
        try {
            const data = await getAppConfig();
            const timeoutValue = data?.llm?.timeout_sec;
            if (timeoutValue !== undefined && timeoutValue !== null) {
                setGlobalTimeoutSec(String(timeoutValue));
            }
            setAgentConfig(normalizeAgentConfig(data?.agent));
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
        try {
            const updated = await updateAppConfig({ llm: { timeout_sec: timeoutValue } });
            if (updated?.llm?.timeout_sec !== undefined && updated?.llm?.timeout_sec !== null) {
                setGlobalTimeoutSec(String(updated.llm.timeout_sec));
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

    return (
        <div className="modal-overlay">
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>配置管理</h2>
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
                            <button className="add-btn" onClick={() => setShowForm(true)}>
                                + Add Config
                            </button>

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
                            <h3>全局配置</h3>

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
                            <h3>Agent配置</h3>

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
                                {showAbilityForm && (
                                    <div className="inline-modal">
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
                                )}
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
                                    <button type="button" className="add-btn add-inline" onClick={openNewProfileForm}>
                                        + Add Profile
                                    </button>
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
