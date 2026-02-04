import { useState, useEffect } from 'react';
import { LLMConfig, LLMConfigCreate, LLMApiFormat, LLMProfile } from '../types';
import { getConfigs, createConfig, updateConfig, deleteConfig, getAppConfig, updateAppConfig } from '../api';
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

export default function ConfigManager({ onClose, onConfigCreated }: ConfigManagerProps) {
    type ConfigTab = 'models' | 'global';
    const [configs, setConfigs] = useState<LLMConfig[]>([]);
    const [activeTab, setActiveTab] = useState<ConfigTab>('models');
    const [globalTimeoutSec, setGlobalTimeoutSec] = useState('180');
    const [globalLoading, setGlobalLoading] = useState(false);
    const [globalSaving, setGlobalSaving] = useState(false);
    const [globalSaved, setGlobalSaved] = useState(false);
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
        is_default: false,
    });

    useEffect(() => {
        loadConfigs();
        loadAppConfig();
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
            is_default: config.is_default,
        });
        setShowForm(true);
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

    return (
        <div className="modal-overlay" onClick={onClose}>
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
                    </div>

                    {activeTab === 'models' ? (
                        !showForm ? (
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
                                                        <strong> Max Tokens:</strong> {config.max_tokens}
                                                    </p>
                                                </div>
                                                <div className="config-actions">
                                                    <button onClick={() => handleEdit(config)}>Edit</button>
                                                    <button onClick={() => handleDelete(config)} className="delete-btn">Delete</button>
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </>
                        ) : (
                            <form onSubmit={handleSubmit} className="config-form">
                                <h3>{editingConfig ? 'Edit Config' : 'Create Config'}</h3>

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
                        )
                    ) : (
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
                    )}
                </div>
            </div>
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
