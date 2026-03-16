import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';

import GraphConfigEditor from './components/GraphConfigEditor';
import { getAppConfig, getTools, updateAppConfig } from './shared/api';
import { normalizeTools } from './features/config/helpers';
import { markGraphStudioUpdated } from './features/graphStudio/sync';
import type { AgentProfile, GraphDefinition, StatePreset, ToolDefinition } from './types';
import './GraphStudioWindow.css';

const GRAPH_STUDIO_BOUNDS_KEY = 'graphStudioWindowBounds';

function resolveDefaultGraphId(graphs: GraphDefinition[], requested?: string | null): string {
    if (!graphs.length) {
        return '';
    }
    if (typeof requested === 'string' && requested.trim()) {
        const candidate = requested.trim();
        if (graphs.some((graph) => graph.id === candidate)) {
            return candidate;
        }
    }
    return graphs[0].id;
}

function formatSavedTime(timestamp: number | null): string {
    if (!timestamp) {
        return '';
    }
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
    });
}

export default function GraphStudioWindow() {
    const appWindow = useMemo(() => getCurrentWindow(), []);
    const [graphs, setGraphs] = useState<GraphDefinition[]>([]);
    const [defaultGraphId, setDefaultGraphId] = useState('');
    const [statePresets, setStatePresets] = useState<StatePreset[]>([]);
    const [profiles, setProfiles] = useState<AgentProfile[]>([]);
    const [tools, setTools] = useState<ToolDefinition[]>([]);
    const [jsonText, setJsonText] = useState('[]');
    const [jsonError, setJsonError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [savedAt, setSavedAt] = useState<number | null>(null);
    const [statusError, setStatusError] = useState<string | null>(null);
    const [loadTick, setLoadTick] = useState(0);
    const mountedRef = useRef(true);

    const loadData = useCallback(async () => {
        setLoading(true);
        setStatusError(null);
        try {
            const [appConfig, toolList] = await Promise.all([getAppConfig(), getTools()]);
            if (!mountedRef.current) {
                return;
            }
            const agent = appConfig?.agent || {};
            const nextGraphs = Array.isArray(agent.graphs) ? agent.graphs : [];
            const nextDefaultGraphId = resolveDefaultGraphId(nextGraphs, agent.default_graph_id);
            const nextStatePresets = Array.isArray(agent.state_presets) ? agent.state_presets : [];
            setGraphs(nextGraphs);
            setDefaultGraphId(nextDefaultGraphId);
            setStatePresets(nextStatePresets);
            setProfiles(Array.isArray(agent.profiles) ? agent.profiles : []);
            setTools(normalizeTools(toolList));
            setJsonText(JSON.stringify(nextGraphs, null, 2));
            setJsonError(null);
            setDirty(false);
            setSavedAt(null);
        } catch (error: any) {
            console.error('Failed to load graph studio data:', error);
            if (mountedRef.current) {
                setStatusError(error?.message || 'Failed to load graph studio data.');
            }
        } finally {
            if (mountedRef.current) {
                setLoading(false);
            }
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void loadData();
        return () => {
            mountedRef.current = false;
        };
    }, [loadData, loadTick]);

    useEffect(() => {
        let stopped = false;
        const saveBounds = async () => {
            try {
                const [position, size] = await Promise.all([appWindow.outerPosition(), appWindow.outerSize()]);
                if (stopped) {
                    return;
                }
                localStorage.setItem(
                    GRAPH_STUDIO_BOUNDS_KEY,
                    JSON.stringify({
                        x: position.x,
                        y: position.y,
                        width: size.width,
                        height: size.height,
                    })
                );
            } catch {
                // ignore bounds errors
            }
        };
        const handleBeforeUnload = () => {
            void saveBounds();
        };
        const interval = window.setInterval(() => {
            void saveBounds();
        }, 1500);
        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => {
            stopped = true;
            window.clearInterval(interval);
            window.removeEventListener('beforeunload', handleBeforeUnload);
        };
    }, [appWindow]);

    const handleGraphsChange = useCallback(
        (nextGraphs: GraphDefinition[], options?: { defaultGraphId?: string; statePresets?: StatePreset[] }) => {
            const nextDefaultGraphId = resolveDefaultGraphId(nextGraphs, options?.defaultGraphId ?? defaultGraphId);
            setGraphs(nextGraphs);
            setDefaultGraphId(nextDefaultGraphId);
            if (options?.statePresets) {
                setStatePresets(options.statePresets);
            }
            setJsonText(JSON.stringify(nextGraphs, null, 2));
            setJsonError(null);
            setDirty(true);
            setSavedAt(null);
            setStatusError(null);
        },
        [defaultGraphId]
    );

    const handleJsonTextChange = useCallback(
        (text: string) => {
            setJsonText(text);
            setSavedAt(null);
            setDirty(true);
            try {
                const parsed = JSON.parse(text || '[]');
                if (!Array.isArray(parsed)) {
                    throw new Error('Graphs JSON must be an array.');
                }
                const nextGraphs = parsed as GraphDefinition[];
                const nextDefaultGraphId = resolveDefaultGraphId(nextGraphs, defaultGraphId);
                setGraphs(nextGraphs);
                setDefaultGraphId(nextDefaultGraphId);
                setJsonError(null);
                setStatusError(null);
            } catch (error: any) {
                setJsonError(error?.message || 'Invalid graphs JSON.');
            }
        },
        [defaultGraphId]
    );

    const handleReload = async () => {
        if (dirty && !window.confirm('当前 Graph Studio 有未保存修改，重新加载会丢失这些修改。是否继续？')) {
            return;
        }
        setLoadTick((prev) => prev + 1);
    };

    const handleSave = async () => {
        setSaving(true);
        setStatusError(null);
        try {
            if (jsonError) {
                throw new Error(`Invalid graphs JSON: ${jsonError}`);
            }
            const parsed = JSON.parse(jsonText || '[]');
            if (!Array.isArray(parsed)) {
                throw new Error('Graphs JSON must be an array.');
            }
            const parsedGraphs = parsed as GraphDefinition[];
            const resolvedDefaultGraphId = resolveDefaultGraphId(parsedGraphs, defaultGraphId);
            const updated = await updateAppConfig({
                agent: {
                    graphs: parsedGraphs,
                    default_graph_id: resolvedDefaultGraphId,
                    state_presets: statePresets,
                },
            });
            const agent = updated?.agent || {};
            const nextGraphs = Array.isArray(agent.graphs) ? agent.graphs : parsedGraphs;
            const nextDefaultGraphId = resolveDefaultGraphId(
                nextGraphs,
                agent.default_graph_id || resolvedDefaultGraphId
            );
            const nextStatePresets = Array.isArray(agent.state_presets) ? agent.state_presets : statePresets;
            setGraphs(nextGraphs);
            setDefaultGraphId(nextDefaultGraphId);
            setStatePresets(nextStatePresets);
            setProfiles(Array.isArray(agent.profiles) ? agent.profiles : profiles);
            setJsonText(JSON.stringify(nextGraphs, null, 2));
            setJsonError(null);
            setDirty(false);
            const now = Date.now();
            setSavedAt(now);
            markGraphStudioUpdated();
        } catch (error: any) {
            console.error('Failed to save graph studio config:', error);
            setStatusError(error?.message || 'Failed to save graph studio config.');
        } finally {
            setSaving(false);
        }
    };

    const defaultGraphName =
        graphs.find((graph) => graph.id === defaultGraphId)?.name || defaultGraphId || '未设置';

    return (
        <div className="graph-studio-shell">
            <header className="graph-studio-masthead">
                <div className="graph-studio-heading">
                    <div className="graph-studio-kicker">Graph Studio</div>
                    <h1>Agent Graph Workbench</h1>
                    <p>在独立窗口里维护 graph、node、edge 和默认 graph。保存会直接写回全局 app config。</p>
                </div>

                <div className="graph-studio-actions">
                    <button type="button" className="graph-studio-btn ghost" onClick={handleReload} disabled={loading || saving}>
                        Reload
                    </button>
                    <button type="button" className="graph-studio-btn primary" onClick={handleSave} disabled={loading || saving}>
                        {saving ? 'Saving...' : dirty ? 'Save Changes' : 'Save'}
                    </button>
                </div>
            </header>

            <section className="graph-studio-summary">
                <div className="graph-studio-stat">
                    <span className="graph-studio-stat-label">Graphs</span>
                    <strong>{graphs.length}</strong>
                </div>
                <div className="graph-studio-stat">
                    <span className="graph-studio-stat-label">Default</span>
                    <strong>{defaultGraphName}</strong>
                </div>
                <div className="graph-studio-stat">
                    <span className="graph-studio-stat-label">Profiles</span>
                    <strong>{profiles.length}</strong>
                </div>
                <div className="graph-studio-stat">
                    <span className="graph-studio-stat-label">Status</span>
                    <strong>{dirty ? 'Unsaved' : savedAt ? `Saved ${formatSavedTime(savedAt)}` : 'Synced'}</strong>
                </div>
            </section>

            {statusError && <div className="graph-studio-banner error">{statusError}</div>}

            <main className="graph-studio-main">
                {loading ? (
                    <div className="graph-studio-loading">
                        <div className="graph-studio-loading-card" />
                        <div className="graph-studio-loading-card" />
                        <div className="graph-studio-loading-card tall" />
                    </div>
                ) : (
                    <GraphConfigEditor
                        graphs={graphs}
                        defaultGraphId={defaultGraphId}
                        statePresets={statePresets}
                        profiles={profiles}
                        tools={tools}
                        jsonText={jsonText}
                        jsonError={jsonError}
                        onGraphsChange={handleGraphsChange}
                        onJsonTextChange={handleJsonTextChange}
                    />
                )}
            </main>
        </div>
    );
}
