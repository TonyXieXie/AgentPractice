import { useState, useEffect } from 'react';
import { LLMConfig, LLMConfigCreate } from '../types';
import { getConfigs, createConfig, updateConfig, deleteConfig } from '../api';
import './ConfigManager.css';

interface ConfigManagerProps {
    onClose: () => void;
    onConfigCreated?: () => void;
}

export default function ConfigManager({ onClose, onConfigCreated }: ConfigManagerProps) {
    const [configs, setConfigs] = useState<LLMConfig[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [editingConfig, setEditingConfig] = useState<LLMConfig | null>(null);
    const [loading, setLoading] = useState(false);

    // Form state
    const [formData, setFormData] = useState<LLMConfigCreate>({
        name: '',
        api_type: 'openai',
        api_key: '',
        base_url: '',
        model: '',
        temperature: 0.7,
        max_tokens: 2000,
        is_default: false,
    });

    useEffect(() => {
        loadConfigs();
    }, []);

    const loadConfigs = async () => {
        try {
            const data = await getConfigs();
            setConfigs(data);
        } catch (error) {
            console.error('Failed to load configs:', error);
            alert('加载配置失败');
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
            // 显示更详细的错误信息
            let errorMessage = '保存配置失败';
            if (error.message) {
                errorMessage += ': ' + error.message;
            }
            if (error.message?.includes('fetch')) {
                errorMessage += '\n\n请检查：\n1. 后端服务是否正在运行？\n2. 访问 http://127.0.0.1:8000 确认后端可访问';
            }
            alert(errorMessage);
        } finally {
            setLoading(false);
        }
    };

    const handleEdit = (config: LLMConfig) => {
        setEditingConfig(config);
        setFormData({
            name: config.name,
            api_type: config.api_type,
            api_key: config.api_key,
            base_url: config.base_url || '',
            model: config.model,
            temperature: config.temperature,
            max_tokens: config.max_tokens,
            is_default: config.is_default,
        });
        setShowForm(true);
    };

    const handleDelete = async (id: string) => {
        if (!confirm('确定要删除这个配置吗？')) return;

        try {
            await deleteConfig(id);
            await loadConfigs();
            onConfigCreated?.();
        } catch (error: any) {
            console.error('Failed to delete config:', error);
            alert(error.message || '删除配置失败');
        }
    };

    const resetForm = () => {
        setFormData({
            name: '',
            api_type: 'openai',
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
        switch (formData.api_type) {
            case 'openai':
                return 'gpt-4, gpt-3.5-turbo';
            case 'zhipu':
                return 'glm-4, glm-3-turbo';
            case 'deepseek':
                return 'deepseek-chat';
            default:
                return '';
        }
    };

    const getBaseUrlPlaceholder = () => {
        switch (formData.api_type) {
            case 'openai':
                return 'https://api.openai.com/v1';
            case 'zhipu':
                return 'https://open.bigmodel.cn/api/paas/v4';
            case 'deepseek':
                return 'https://api.deepseek.com/v1';
            default:
                return '';
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>⚙️ LLM 配置管理</h2>
                    <button className="close-btn" onClick={onClose}>✕</button>
                </div>

                <div className="modal-body">
                    {!showForm ? (
                        <>
                            <button className="add-btn" onClick={() => setShowForm(true)}>
                                ➕ 添加新配置
                            </button>

                            <div className="configs-list">
                                {configs.length === 0 ? (
                                    <p className="empty-message">暂无配置，请添加一个</p>
                                ) : (
                                    configs.map((config) => (
                                        <div key={config.id} className="config-item">
                                            <div className="config-info">
                                                <h3>
                                                    {config.name}
                                                    {config.is_default && <span className="badge">默认</span>}
                                                </h3>
                                                <p className="config-detail">
                                                    <strong>类型:</strong> {config.api_type.toUpperCase()} |
                                                    <strong> 模型:</strong> {config.model}
                                                </p>
                                                <p className="config-detail">
                                                    <strong>温度:</strong> {config.temperature} |
                                                    <strong> 最大tokens:</strong> {config.max_tokens}
                                                </p>
                                            </div>
                                            <div className="config-actions">
                                                <button onClick={() => handleEdit(config)}>编辑</button>
                                                <button onClick={() => handleDelete(config.id)} className="delete-btn">删除</button>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </>
                    ) : (
                        <form onSubmit={handleSubmit} className="config-form">
                            <h3>{editingConfig ? '编辑配置' : '新建配置'}</h3>

                            <div className="form-group">
                                <label>配置名称 *</label>
                                <input
                                    type="text"
                                    value={formData.name}
                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                    placeholder="例如: OpenAI GPT-4"
                                    required
                                />
                            </div>

                            <div className="form-group">
                                <label>API 类型 *</label>
                                <select
                                    value={formData.api_type}
                                    onChange={(e) => setFormData({ ...formData, api_type: e.target.value as any })}
                                    required
                                >
                                    <option value="openai">OpenAI</option>
                                    <option value="zhipu">智谱AI (GLM)</option>
                                    <option value="deepseek">Deepseek</option>
                                </select>
                            </div>

                            <div className="form-group">
                                <label>API Key *</label>
                                <input
                                    type="password"
                                    value={formData.api_key}
                                    onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                                    placeholder="输入 API Key"
                                    required
                                />
                            </div>

                            <div className="form-group">
                                <label>Base URL (可选)</label>
                                <input
                                    type="text"
                                    value={formData.base_url}
                                    onChange={(e) => setFormData({ ...formData, base_url: e.target.value })}
                                    placeholder={getBaseUrlPlaceholder()}
                                />
                                <small>留空使用默认地址</small>
                            </div>

                            <div className="form-group">
                                <label>模型名称 *</label>
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
                                    <label>温度 (0-2)</label>
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
                                    <label>最大 Tokens</label>
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
                                    设为默认配置
                                </label>
                            </div>

                            <div className="form-actions">
                                <button type="button" onClick={resetForm} disabled={loading}>
                                    取消
                                </button>
                                <button type="submit" disabled={loading}>
                                    {loading ? '保存中...' : '保存'}
                                </button>
                            </div>
                        </form>
                    )}
                </div>
            </div>
        </div>
    );
}
