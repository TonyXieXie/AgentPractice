import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Background,
    BackgroundVariant,
    Controls,
    Handle,
    MarkerType,
    MiniMap,
    Position,
    ReactFlow,
    type Connection,
    type Edge,
    type Node,
    type OnMoveEnd,
    type OnNodeDrag,
    type NodeProps,
    type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type {
    AgentProfile,
    GraphDefinition,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    StateFieldDefinition,
    StateFieldType,
    StatePreset,
    ToolDefinition,
} from '../types';
import { validateGraphConditionExpression } from '../features/config/graphExpression';
import './GraphConfigEditor.css';

const GRAPH_START = '__start__';
const GRAPH_END = '__end__';
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 0.85 };
const HORIZONTAL_SPACING = 280;
const VERTICAL_SPACING = 170;
const RESERVED_STATE_ROOTS = ['input', 'messages'] as const;
const GRAPH_TEMPLATE_ALLOWED_ROOTS = ['state', 'result', 'session'] as const;
const GRAPH_TEMPLATE_PATTERN = /\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}/g;

type GraphSelection =
    | { kind: 'graph' }
    | { kind: 'node'; nodeId: string }
    | { kind: 'edge'; edgeId: string }
    | { kind: 'statePreset'; presetId: string };

type GraphEditorChangeOptions = {
    defaultGraphId?: string;
    statePresets?: StatePreset[];
};

type GraphCanvasNodeData = {
    title: string;
    summary: string;
    nodeType: GraphNodeType | 'start' | 'end';
};

type GraphConfigEditorProps = {
    graphs: GraphDefinition[];
    defaultGraphId?: string;
    statePresets: StatePreset[];
    profiles: AgentProfile[];
    tools: ToolDefinition[];
    jsonText: string;
    jsonError?: string | null;
    onGraphsChange: (graphs: GraphDefinition[], options?: GraphEditorChangeOptions) => void;
    onJsonTextChange: (text: string) => void;
};

type StateFieldEntry = {
    path: string;
    type: StateFieldType;
    mutable?: boolean;
    value: unknown;
};

type StateFieldEditorProps = {
    stateValue: unknown;
    stateSchema?: StateFieldDefinition[];
    addFieldDraft?: string;
    addFieldType?: StateFieldType;
    onAddFieldDraftChange?: (value: string) => void;
    onAddFieldTypeChange?: (value: StateFieldType) => void;
    onAddField?: () => void;
    onFieldTypeChange?: (path: string, type: StateFieldType) => void;
    onFieldMutableChange?: (path: string, mutable: boolean) => void;
    onRemoveField?: (path: string) => void;
    readOnly?: boolean;
    showAdvancedJson?: boolean;
    onToggleAdvancedJson?: () => void;
    jsonText?: string;
    jsonError?: string | null;
    onJsonChange?: (text: string) => void;
    emptyMessage: string;
    advancedLabel?: string;
};

const DEFAULT_STATE_FIELD_TYPE: StateFieldType = 'string';
const STATE_FIELD_TYPE_OPTIONS: Array<{ value: StateFieldType; label: string }> = [
    { value: 'string', label: 'string' },
    { value: 'number', label: 'number' },
    { value: 'boolean', label: 'boolean' },
    { value: 'object', label: 'object' },
    { value: 'array', label: 'array' },
    { value: 'any', label: 'any' },
];

function cloneJson<T>(value: T): T {
    return JSON.parse(JSON.stringify(value));
}

function slugifyId(raw: string, fallbackPrefix: string): string {
    const base = String(raw || '')
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9_-]+/g, '-')
        .replace(/-{2,}/g, '-')
        .replace(/(^-|-$)/g, '');
    return base || `${fallbackPrefix}-${Date.now()}`;
}

function makeUniqueId(raw: string, existing: string[], fallbackPrefix: string): string {
    const base = slugifyId(raw, fallbackPrefix);
    let nextId = base;
    let counter = 2;
    while (existing.includes(nextId)) {
        nextId = `${base}-${counter}`;
        counter += 1;
    }
    return nextId;
}

function ensureDefaultGraphId(graphs: GraphDefinition[], requested?: string | null): string {
    if (!graphs.length) {
        return '';
    }
    if (requested && graphs.some((graph) => graph.id === requested)) {
        return requested;
    }
    return graphs[0].id;
}

function formatFlexibleValue(value: unknown): string {
    if (value === undefined || value === null) {
        return '';
    }
    if (typeof value === 'string') {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function parseFlexibleValue(value: string): unknown {
    const trimmed = value.trim();
    if (!trimmed) {
        return '';
    }
    try {
        return JSON.parse(trimmed);
    } catch {
        return value;
    }
}

function formatGraphState(value: unknown): string {
    try {
        return JSON.stringify(value ?? {}, null, 2);
    } catch {
        return '{}';
    }
}

function isEditableShortcutTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) {
        return false;
    }
    return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
}

function summarizeGraphNode(node: GraphNode): string {
    if (node.type === 'react_agent') {
        return node.profile_id ? `profile: ${node.profile_id}` : 'ReAct agent';
    }
    if (node.type === 'tool_call') {
        return node.tool_name ? `tool: ${node.tool_name}` : 'Tool call';
    }
    return 'Branch router';
}

function getNextNodePosition(graph: GraphDefinition): { x: number; y: number } {
    const positions = (graph.nodes || [])
        .map((node) => node.ui?.position)
        .filter(Boolean) as Array<{ x: number; y: number }>;
    if (!positions.length) {
        return { x: 320, y: 100 };
    }
    const maxX = Math.max(...positions.map((position) => position.x));
    const maxY = Math.max(...positions.map((position) => position.y));
    return { x: maxX + 80, y: maxY + 140 };
}

function createStarterGraph(existingGraphs: GraphDefinition[]): GraphDefinition {
    const graphId = makeUniqueId('graph', existingGraphs.map((graph) => graph.id), 'graph');
    const reactNodeId = 'react-main';
    return {
        id: graphId,
        name: `Graph ${existingGraphs.length + 1}`,
        initial_state: {
            message: null,
            current_task: null,
        },
        state_schema: [
            { path: 'message', type: 'string', mutable: false },
            { path: 'current_task', type: 'string', mutable: false },
        ],
        max_hops: 100,
        ui: {
            viewport: cloneJson(DEFAULT_VIEWPORT),
        },
        nodes: [
            {
                id: reactNodeId,
                type: 'react_agent',
                name: 'Main Agent',
                input_template: '{{state.input.user_message}}',
                output_path: 'last_answer',
                ui: {
                    position: { x: 320, y: 110 },
                },
            },
        ],
        edges: [
            {
                id: 'start_to_react-main',
                source: GRAPH_START,
                target: reactNodeId,
                priority: 0,
            },
            {
                id: 'react-main_to_end',
                source: reactNodeId,
                target: GRAPH_END,
                priority: 0,
            },
        ],
    };
}

function createStarterStatePreset(existingPresets: StatePreset[]): StatePreset {
    const presetId = makeUniqueId('state', existingPresets.map((preset) => preset.id), 'state');
    return {
        id: presetId,
        name: `State ${existingPresets.length + 1}`,
        description: '',
        state: {
            message: null,
            current_task: null,
        },
        state_schema: [
            { path: 'message', type: 'string', mutable: false },
            { path: 'current_task', type: 'string', mutable: false },
        ],
    };
}

function layoutGraph(graph: GraphDefinition): GraphDefinition {
    const nextGraph = cloneJson(graph);
    const outgoing = new Map<string, string[]>();
    for (const edge of nextGraph.edges || []) {
        if (!outgoing.has(edge.source)) {
            outgoing.set(edge.source, []);
        }
        outgoing.get(edge.source)!.push(edge.target);
    }

    const levels = new Map<string, number>([[GRAPH_START, 0]]);
    const queue = [GRAPH_START];
    while (queue.length) {
        const source = queue.shift()!;
        const sourceLevel = levels.get(source) ?? 0;
        for (const target of outgoing.get(source) || []) {
            if (target === GRAPH_END || levels.has(target)) {
                continue;
            }
            levels.set(target, sourceLevel + 1);
            queue.push(target);
        }
    }

    let fallbackLevel = Math.max(1, ...Array.from(levels.values()));
    const grouped = new Map<number, GraphNode[]>();
    for (const node of nextGraph.nodes || []) {
        const level = levels.get(node.id) ?? fallbackLevel;
        if (!levels.has(node.id)) {
            fallbackLevel += 1;
        }
        if (!grouped.has(level)) {
            grouped.set(level, []);
        }
        grouped.get(level)!.push(node);
    }

    const sortedLevels = Array.from(grouped.keys()).sort((a, b) => a - b);
    for (const level of sortedLevels) {
        const nodes = grouped.get(level) || [];
        nodes.forEach((node, index) => {
            if (!node.ui?.position) {
                node.ui = {
                    ...(node.ui || {}),
                    position: {
                        x: 220 + level * HORIZONTAL_SPACING,
                        y: 80 + index * VERTICAL_SPACING,
                    },
                };
            }
        });
    }

    if (!nextGraph.ui?.viewport) {
        nextGraph.ui = {
            ...(nextGraph.ui || {}),
            viewport: cloneJson(DEFAULT_VIEWPORT),
        };
    }

    return nextGraph;
}

function graphNeedsLayout(graph: GraphDefinition | null | undefined): boolean {
    if (!graph) {
        return false;
    }
    if (!graph.ui?.viewport) {
        return true;
    }
    return (graph.nodes || []).some((node) => !node.ui?.position);
}

function isPlainObject(value: unknown): value is Record<string, any> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function normalizeStatePath(path: string): string {
    return String(path || '')
        .trim()
        .replace(/\s+/g, '')
        .replace(/\.+/g, '.')
        .replace(/^\./, '')
        .replace(/\.$/, '');
}

function isReservedStatePath(path: string): boolean {
    const root = normalizeStatePath(path).split('.', 1)[0];
    return RESERVED_STATE_ROOTS.includes(root as (typeof RESERVED_STATE_ROOTS)[number]);
}

function getReservedStateValueError(value: unknown): string | null {
    if (!isPlainObject(value)) {
        return null;
    }
    const conflicts = RESERVED_STATE_ROOTS.filter((root) => root in value);
    if (!conflicts.length) {
        return null;
    }
    return `Reserved runtime state roots are managed automatically: ${conflicts.join(', ')}.`;
}

function validateGraphTemplateValue(value: unknown): string | null {
    if (typeof value === 'string') {
        GRAPH_TEMPLATE_PATTERN.lastIndex = 0;
        let match = GRAPH_TEMPLATE_PATTERN.exec(value);
        while (match) {
            const expression = String(match[1] || '').trim();
            const root = expression.split('.', 1)[0];
            if (!GRAPH_TEMPLATE_ALLOWED_ROOTS.includes(root as (typeof GRAPH_TEMPLATE_ALLOWED_ROOTS)[number])) {
                return `Only ${GRAPH_TEMPLATE_ALLOWED_ROOTS.join(', ')} template roots are allowed in graph mode.`;
            }
            match = GRAPH_TEMPLATE_PATTERN.exec(value);
        }
        return null;
    }
    if (Array.isArray(value)) {
        for (const item of value) {
            const error = validateGraphTemplateValue(item);
            if (error) {
                return error;
            }
        }
        return null;
    }
    if (isPlainObject(value)) {
        for (const item of Object.values(value)) {
            const error = validateGraphTemplateValue(item);
            if (error) {
                return error;
            }
        }
    }
    return null;
}

function flattenStateFields(value: unknown, prefix = ''): StateFieldEntry[] {
    if (isPlainObject(value)) {
        const entries = Object.entries(value);
        if (!entries.length) {
            return prefix ? [{ path: prefix, type: 'object', value }] : [];
        }
        return entries.flatMap(([key, entryValue]) =>
            flattenStateFields(entryValue, prefix ? `${prefix}.${key}` : key)
        );
    }
    if (Array.isArray(value)) {
        return prefix ? [{ path: prefix, type: 'array', value }] : [];
    }
    return prefix ? [{ path: prefix, type: inferStateFieldType(value), value }] : [];
}

function hasStateField(value: unknown, path: string): boolean {
    if (!path) {
        return false;
    }
    const parts = path.split('.');
    let current: unknown = value;
    for (const part of parts) {
        if (!isPlainObject(current) || !(part in current)) {
            return false;
        }
        current = current[part];
    }
    return true;
}

function setStateField(value: unknown, path: string, nextValue: unknown): Record<string, any> {
    const root = isPlainObject(value) ? cloneJson(value) : {};
    const parts = normalizeStatePath(path).split('.').filter(Boolean);
    if (!parts.length) {
        return root;
    }
    let current: Record<string, any> = root;
    parts.forEach((part, index) => {
        const isLast = index === parts.length - 1;
        if (isLast) {
            current[part] = cloneJson(nextValue);
            return;
        }
        if (!isPlainObject(current[part])) {
            current[part] = {};
        }
        current = current[part];
    });
    return root;
}

function pruneEmptyStateParents(value: unknown): unknown {
    if (!isPlainObject(value)) {
        return value;
    }
    const next: Record<string, any> = {};
    for (const [key, entryValue] of Object.entries(value)) {
        const pruned = pruneEmptyStateParents(entryValue);
        if (isPlainObject(pruned) && !Object.keys(pruned).length) {
            continue;
        }
        next[key] = pruned;
    }
    return next;
}

function removeStateField(value: unknown, path: string): Record<string, any> {
    if (!isPlainObject(value)) {
        return {};
    }
    const parts = normalizeStatePath(path).split('.').filter(Boolean);
    if (!parts.length) {
        return cloneJson(value);
    }
    const root = cloneJson(value);
    const stack: Record<string, any>[] = [root];
    let current: Record<string, any> | undefined = root;

    for (let index = 0; index < parts.length - 1; index += 1) {
        const part = parts[index];
        if (!isPlainObject(current?.[part])) {
            return root;
        }
        current = current?.[part];
        stack.push(current);
    }

    if (!current) {
        return root;
    }
    delete current[parts[parts.length - 1]];

    for (let index = stack.length - 1; index > 0; index -= 1) {
        const child = stack[index];
        if (Object.keys(child).length) {
            continue;
        }
        const parent = stack[index - 1];
        delete parent[parts[index - 1]];
    }

    return pruneEmptyStateParents(root) as Record<string, any>;
}

function formatStateFieldValue(value: unknown): string {
    if (value === null) {
        return 'null';
    }
    if (value === undefined) {
        return 'undefined';
    }
    if (typeof value === 'string') {
        return value || '""';
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
    }
    try {
        return JSON.stringify(value);
    } catch {
        return String(value);
    }
}

function inferStateFieldType(value: unknown): StateFieldType {
    if (Array.isArray(value)) {
        return 'array';
    }
    if (isPlainObject(value)) {
        return 'object';
    }
    if (typeof value === 'string') {
        return 'string';
    }
    if (typeof value === 'number') {
        return 'number';
    }
    if (typeof value === 'boolean') {
        return 'boolean';
    }
    return 'any';
}

function normalizeStateSchema(schema: StateFieldDefinition[] | undefined, stateValue: unknown): StateFieldDefinition[] {
    const normalizedPaths = new Map<string, { type: StateFieldType; mutable: boolean }>();
    for (const field of schema || []) {
        const path = normalizeStatePath(field.path);
        if (!path || normalizedPaths.has(path)) {
            continue;
        }
        normalizedPaths.set(path, {
            type: field.type || DEFAULT_STATE_FIELD_TYPE,
            mutable: Boolean(field.mutable),
        });
    }
    return flattenStateFields(stateValue).map((field) => ({
        path: field.path,
        type: normalizedPaths.get(field.path)?.type || field.type || DEFAULT_STATE_FIELD_TYPE,
        mutable: normalizedPaths.get(field.path)?.mutable || false,
    }));
}

function buildStateFieldEntries(stateValue: unknown, stateSchema?: StateFieldDefinition[]): StateFieldEntry[] {
    const schemaMap = new Map<string, { type: StateFieldType; mutable: boolean }>(
        normalizeStateSchema(stateSchema, stateValue).map((field) => [
            field.path,
            { type: field.type, mutable: Boolean(field.mutable) },
        ])
    );
    return flattenStateFields(stateValue).map((field) => ({
        path: field.path,
        type: schemaMap.get(field.path)?.type || field.type || DEFAULT_STATE_FIELD_TYPE,
        mutable: schemaMap.get(field.path)?.mutable || false,
        value: field.value,
    }));
}

function updateStateFieldType(
    stateSchema: StateFieldDefinition[] | undefined,
    stateValue: unknown,
    path: string,
    type: StateFieldType
): StateFieldDefinition[] {
    return normalizeStateSchema(stateSchema, stateValue).map((field) =>
        field.path === path ? { ...field, type } : field
    );
}

function updateStateFieldMutable(
    stateSchema: StateFieldDefinition[] | undefined,
    stateValue: unknown,
    path: string,
    mutable: boolean
): StateFieldDefinition[] {
    return normalizeStateSchema(stateSchema, stateValue).map((field) =>
        field.path === path ? { ...field, mutable } : field
    );
}

function getGraphEffectiveStateSchema(graph: GraphDefinition | null, statePresets: StatePreset[]): StateFieldDefinition[] {
    if (!graph) {
        return [];
    }
    const linkedPreset = findStatePreset(statePresets, graph.state_preset_id);
    if (linkedPreset) {
        return normalizeStateSchema(linkedPreset.state_schema, linkedPreset.state);
    }
    return normalizeStateSchema(graph.state_schema, graph.initial_state);
}

function getDefaultStateValueForType(type: StateFieldType): unknown {
    if (type === 'object') {
        return {};
    }
    if (type === 'array') {
        return [];
    }
    return null;
}

function findStatePreset(statePresets: StatePreset[], presetId?: string | null): StatePreset | null {
    if (!presetId) {
        return null;
    }
    return statePresets.find((preset) => preset.id === presetId) || null;
}

function getGraphEffectiveState(graph: GraphDefinition | null, statePresets: StatePreset[]): unknown {
    if (!graph) {
        return {};
    }
    const linkedPreset = findStatePreset(statePresets, graph.state_preset_id);
    if (linkedPreset) {
        return linkedPreset.state ?? {};
    }
    return graph.initial_state ?? {};
}

function syncGraphsToPresetSnapshot(
    graphs: GraphDefinition[],
    presetId: string,
    stateValue: unknown,
    stateSchema?: StateFieldDefinition[]
): GraphDefinition[] {
    return graphs.map((graph) =>
        graph.state_preset_id === presetId
            ? {
                  ...graph,
                  initial_state: cloneJson(stateValue),
                  state_schema: normalizeStateSchema(stateSchema, stateValue),
              }
            : graph
    );
}

function getPresetUsageCount(graphs: GraphDefinition[], presetId: string): number {
    return graphs.filter((graph) => graph.state_preset_id === presetId).length;
}

function GraphCanvasNode({ data, selected }: NodeProps<Node<GraphCanvasNodeData>>) {
    const isStart = data.nodeType === 'start';
    const isEnd = data.nodeType === 'end';
    return (
        <div className={`graph-editor-node graph-editor-node-${data.nodeType}${selected ? ' selected' : ''}`}>
            {!isStart && <Handle type="target" position={Position.Left} />}
            {!isEnd && <Handle type="source" position={Position.Right} />}
            <div className="graph-editor-node-kicker">
                {isStart ? 'START' : isEnd ? 'END' : data.nodeType.replace('_', ' ')}
            </div>
            <div className="graph-editor-node-title">{data.title}</div>
            <div className="graph-editor-node-summary">{data.summary}</div>
        </div>
    );
}

function StateFieldEditor({
    stateValue,
    stateSchema,
    addFieldDraft = '',
    addFieldType = DEFAULT_STATE_FIELD_TYPE,
    onAddFieldDraftChange,
    onAddFieldTypeChange,
    onAddField,
    onFieldTypeChange,
    onFieldMutableChange,
    onRemoveField,
    readOnly = false,
    showAdvancedJson = false,
    onToggleAdvancedJson,
    jsonText = '{}',
    jsonError,
    onJsonChange,
    emptyMessage,
    advancedLabel = 'Advanced JSON',
}: StateFieldEditorProps) {
    const fields = buildStateFieldEntries(stateValue, stateSchema);
    const rootIsObject = isPlainObject(stateValue);

    return (
        <div className="graph-editor-state-editor">
            {!readOnly && (
                <div className="graph-editor-state-builder">
                    <div className="graph-editor-state-builder-row">
                        <input
                            type="text"
                            value={addFieldDraft}
                            onChange={(event) => onAddFieldDraftChange?.(event.target.value)}
                            placeholder="state path, e.g. user.profile.name"
                        />
                        <select
                            value={addFieldType}
                            onChange={(event) => onAddFieldTypeChange?.(event.target.value as StateFieldType)}
                        >
                            {STATE_FIELD_TYPE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                    {option.label}
                                </option>
                            ))}
                        </select>
                        <button type="button" className="add-btn add-inline" onClick={onAddField}>
                            Add Field
                        </button>
                    </div>
                    <small>
                        Adding a field creates the path, stores the type definition, and seeds a default value.
                        New fields start as read-only until you mark them mutable.
                    </small>
                </div>
            )}

            {!rootIsObject && !readOnly && (
                <div className="graph-editor-state-note">
                    Current state root is not an object. Adding a field will convert it to an object root.
                </div>
            )}
            <div className="graph-editor-state-note">
                Runtime-managed paths `input.*` and `messages` are read-only and cannot be defined here.
            </div>

            {fields.length ? (
                <div className="graph-editor-state-field-list">
                    {fields.map((field) => (
                        <div key={field.path} className="graph-editor-state-field-item">
                            <div className="graph-editor-state-field-copy">
                                <div className="graph-editor-state-field-path">{field.path}</div>
                                <div className="graph-editor-state-field-meta">
                                    {readOnly ? (
                                        <>
                                            <span className="graph-editor-state-type-badge">{field.type}</span>
                                            <span className="graph-editor-state-type-badge">
                                                {field.mutable ? 'mutable' : 'read-only'}
                                            </span>
                                        </>
                                    ) : (
                                        <>
                                            <select
                                                className="graph-editor-state-field-type"
                                                value={field.type}
                                                onChange={(event) =>
                                                    onFieldTypeChange?.(field.path, event.target.value as StateFieldType)
                                                }
                                            >
                                                {STATE_FIELD_TYPE_OPTIONS.map((option) => (
                                                    <option key={option.value} value={option.value}>
                                                        {option.label}
                                                    </option>
                                                ))}
                                            </select>
                                            <label className="graph-editor-state-mutable-toggle">
                                                <input
                                                    type="checkbox"
                                                    checked={field.mutable}
                                                    onChange={(event) =>
                                                        onFieldMutableChange?.(field.path, event.target.checked)
                                                    }
                                                />
                                                Mutable
                                            </label>
                                        </>
                                    )}
                                    <div className="graph-editor-state-field-value">
                                        {formatStateFieldValue(field.value)}
                                    </div>
                                </div>
                            </div>
                            {!readOnly && (
                                <button
                                    type="button"
                                    className="add-btn add-inline danger"
                                    onClick={() => onRemoveField?.(field.path)}
                                >
                                    Remove
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            ) : (
                <div className="graph-editor-state-empty">{emptyMessage}</div>
            )}

            {onJsonChange && onToggleAdvancedJson && (
                <div className="graph-editor-state-advanced">
                    <button type="button" className="add-btn add-inline" onClick={onToggleAdvancedJson}>
                        {showAdvancedJson ? `Hide ${advancedLabel}` : `Show ${advancedLabel}`}
                    </button>
                    {showAdvancedJson && (
                        <div className="graph-editor-state-json">
                            <textarea
                                rows={8}
                                value={jsonText}
                                onChange={(event) => onJsonChange(event.target.value)}
                            />
                            {jsonError ? (
                                <div className="form-error">{jsonError}</div>
                            ) : (
                                <small>Edit the raw JSON when you need custom values or structures.</small>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

const nodeTypes = {
    graphCard: GraphCanvasNode,
};

export default function GraphConfigEditor({
    graphs,
    defaultGraphId,
    statePresets,
    profiles,
    tools,
    jsonText,
    jsonError,
    onGraphsChange,
    onJsonTextChange,
}: GraphConfigEditorProps) {
    const [selectedGraphId, setSelectedGraphId] = useState<string | null>(
        defaultGraphId || graphs[0]?.id || null
    );
    const [selection, setSelection] = useState<GraphSelection>({ kind: 'graph' });
    const [showAdvancedJson, setShowAdvancedJson] = useState(false);
    const [showGraphStateAdvancedJson, setShowGraphStateAdvancedJson] = useState(false);
    const [showPresetStateAdvancedJson, setShowPresetStateAdvancedJson] = useState(false);
    const [graphStateText, setGraphStateText] = useState('{}');
    const [graphStateError, setGraphStateError] = useState<string | null>(null);
    const [presetStateText, setPresetStateText] = useState('{}');
    const [presetStateError, setPresetStateError] = useState<string | null>(null);
    const [graphFieldDraft, setGraphFieldDraft] = useState('');
    const [presetFieldDraft, setPresetFieldDraft] = useState('');
    const [graphFieldTypeDraft, setGraphFieldTypeDraft] = useState<StateFieldType>(DEFAULT_STATE_FIELD_TYPE);
    const [presetFieldTypeDraft, setPresetFieldTypeDraft] = useState<StateFieldType>(DEFAULT_STATE_FIELD_TYPE);

    const sortedProfiles = useMemo(
        () => [...profiles].sort((left, right) => left.name.localeCompare(right.name)),
        [profiles]
    );
    const sortedTools = useMemo(
        () => [...tools].sort((left, right) => left.name.localeCompare(right.name)),
        [tools]
    );

    const resolvedDefaultGraphId = useMemo(
        () => ensureDefaultGraphId(graphs, defaultGraphId),
        [defaultGraphId, graphs]
    );

    useEffect(() => {
        const desiredGraphId =
            (selectedGraphId && graphs.some((graph) => graph.id === selectedGraphId) ? selectedGraphId : null) ||
            resolvedDefaultGraphId ||
            graphs[0]?.id ||
            null;
        if (desiredGraphId !== selectedGraphId) {
            setSelectedGraphId(desiredGraphId);
            setSelection({ kind: 'graph' });
        }
    }, [graphs, resolvedDefaultGraphId, selectedGraphId]);

    const currentGraph = useMemo(
        () => graphs.find((graph) => graph.id === selectedGraphId) || null,
        [graphs, selectedGraphId]
    );

    useEffect(() => {
        if (!currentGraph) {
            setGraphStateText('{}');
            setGraphStateError(null);
            setGraphFieldDraft('');
            setGraphFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
            return;
        }
        setGraphStateText(formatGraphState(currentGraph.initial_state));
        setGraphStateError(null);
        setGraphFieldDraft('');
        setGraphFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
    }, [currentGraph]);

    const selectedPreset = useMemo(() => {
        if (selection.kind !== 'statePreset') {
            return null;
        }
        return findStatePreset(statePresets, selection.presetId);
    }, [selection, statePresets]);

    const linkedPreset = useMemo(
        () => findStatePreset(statePresets, currentGraph?.state_preset_id),
        [currentGraph?.state_preset_id, statePresets]
    );

    const effectiveGraphState = useMemo(
        () => getGraphEffectiveState(currentGraph, statePresets),
        [currentGraph, statePresets]
    );

    const effectiveGraphStateSchema = useMemo(
        () => getGraphEffectiveStateSchema(currentGraph, statePresets),
        [currentGraph, statePresets]
    );

    useEffect(() => {
        if (!selectedPreset) {
            setPresetStateText('{}');
            setPresetStateError(null);
            setPresetFieldDraft('');
            setPresetFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
            return;
        }
        setPresetStateText(formatGraphState(selectedPreset.state));
        setPresetStateError(null);
        setPresetFieldDraft('');
        setPresetFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
    }, [selectedPreset]);

    const applyStudio = useCallback(
        (
            nextGraphs: GraphDefinition[],
            nextStatePresets: StatePreset[] = statePresets,
            options?: GraphEditorChangeOptions
        ) => {
            const resolved = ensureDefaultGraphId(nextGraphs, options?.defaultGraphId ?? resolvedDefaultGraphId);
            onGraphsChange(nextGraphs, {
                defaultGraphId: resolved,
                statePresets: options?.statePresets ?? nextStatePresets,
            });
        },
        [onGraphsChange, resolvedDefaultGraphId, statePresets]
    );

    useEffect(() => {
        if (!currentGraph || !graphNeedsLayout(currentGraph)) {
            return;
        }
        const nextGraphs = graphs.map((graph) => (graph.id === currentGraph.id ? layoutGraph(graph) : graph));
        applyStudio(nextGraphs, statePresets, { defaultGraphId: resolvedDefaultGraphId });
    }, [applyStudio, currentGraph, graphs, resolvedDefaultGraphId, statePresets]);

    const updateCurrentGraph = useCallback(
        (updater: (graph: GraphDefinition) => GraphDefinition) => {
            if (!currentGraph) {
                return;
            }
            const nextGraphs = graphs.map((graph) => (graph.id === currentGraph.id ? updater(cloneJson(graph)) : graph));
            applyStudio(nextGraphs, statePresets, { defaultGraphId: resolvedDefaultGraphId });
        },
        [applyStudio, currentGraph, graphs, resolvedDefaultGraphId, statePresets]
    );

    const updateStatePresets = useCallback(
        (nextStatePresets: StatePreset[], nextGraphs: GraphDefinition[] = graphs) => {
            applyStudio(nextGraphs, nextStatePresets, { defaultGraphId: resolvedDefaultGraphId });
        },
        [applyStudio, graphs, resolvedDefaultGraphId]
    );

    const currentNode = useMemo(() => {
        if (selection.kind !== 'node' || !currentGraph) {
            return null;
        }
        return currentGraph.nodes.find((node) => node.id === selection.nodeId) || null;
    }, [currentGraph, selection]);

    const currentEdge = useMemo(() => {
        if (selection.kind !== 'edge' || !currentGraph) {
            return null;
        }
        return currentGraph.edges.find((edge) => edge.id === selection.edgeId) || null;
    }, [currentGraph, selection]);

    useEffect(() => {
        if (selection.kind === 'node' && currentGraph && !currentGraph.nodes.some((node) => node.id === selection.nodeId)) {
            setSelection({ kind: 'graph' });
            return;
        }
        if (selection.kind === 'edge' && currentGraph && !currentGraph.edges.some((edge) => edge.id === selection.edgeId)) {
            setSelection({ kind: 'graph' });
            return;
        }
        if (selection.kind === 'statePreset' && !statePresets.some((preset) => preset.id === selection.presetId)) {
            setSelection({ kind: 'graph' });
        }
    }, [currentGraph, selection, statePresets]);

    const conditionError = useMemo(() => {
        if (!currentEdge?.condition) {
            return null;
        }
        return validateGraphConditionExpression(currentEdge.condition);
    }, [currentEdge]);

    const currentNodeInputTemplateError = useMemo(() => {
        if (!currentNode || currentNode.type !== 'react_agent') {
            return null;
        }
        return validateGraphTemplateValue(currentNode.input_template);
    }, [currentNode]);

    const currentNodeArgsTemplateError = useMemo(() => {
        if (!currentNode || currentNode.type !== 'tool_call') {
            return null;
        }
        return validateGraphTemplateValue(currentNode.args_template);
    }, [currentNode]);

    const currentNodeOutputPathError = useMemo(() => {
        if (!currentNode?.output_path) {
            return null;
        }
        return isReservedStatePath(currentNode.output_path)
            ? 'Reserved runtime paths `input.*` and `messages` cannot be used as output_path.'
            : null;
    }, [currentNode]);

    const canvasNodes = useMemo(() => {
        if (!currentGraph) {
            return [] as Array<Node<GraphCanvasNodeData>>;
        }
        const positionedGraph = graphNeedsLayout(currentGraph) ? layoutGraph(currentGraph) : currentGraph;
        const businessNodes: Array<Node<GraphCanvasNodeData>> = positionedGraph.nodes.map((node) => ({
            id: node.id,
            type: 'graphCard',
            position: node.ui?.position || { x: 260, y: 100 },
            data: {
                title: node.name || node.id,
                summary: summarizeGraphNode(node),
                nodeType: node.type,
            },
        }));
        const maxX = businessNodes.length ? Math.max(...businessNodes.map((node) => node.position.x)) : 320;
        const startNode: Node<GraphCanvasNodeData> = {
            id: GRAPH_START,
            type: 'graphCard',
            draggable: false,
            selectable: true,
            position: { x: 40, y: 120 },
            data: {
                title: 'Graph Start',
                summary: 'Entry edge origin',
                nodeType: 'start',
            },
        };
        const endNode: Node<GraphCanvasNodeData> = {
            id: GRAPH_END,
            type: 'graphCard',
            draggable: false,
            selectable: true,
            position: { x: maxX + HORIZONTAL_SPACING, y: 120 },
            data: {
                title: 'Graph End',
                summary: 'Terminal edge target',
                nodeType: 'end',
            },
        };
        return [
            startNode,
            ...businessNodes,
            endNode,
        ];
    }, [currentGraph]);

    const canvasEdges = useMemo<Edge[]>(() => {
        if (!currentGraph) {
            return [];
        }
        return (currentGraph.edges || []).map((edge) => ({
            id: edge.id || `${edge.source}_to_${edge.target}`,
            source: edge.source,
            target: edge.target,
            label: edge.label || edge.condition || undefined,
            animated: Boolean(edge.condition),
            markerEnd: { type: MarkerType.ArrowClosed },
            style: {
                stroke: edge.condition ? '#f59e0b' : '#7dd3fc',
                strokeWidth: 1.6,
            },
            labelStyle: {
                fill: '#f8fafc',
                fontSize: 11,
                fontWeight: 600,
            },
            labelBgStyle: {
                fill: 'rgba(15, 23, 42, 0.92)',
                fillOpacity: 1,
            },
            labelBgPadding: [6, 3] as [number, number],
            labelBgBorderRadius: 5,
        }));
    }, [currentGraph]);

    const addGraph = useCallback(() => {
        const nextGraph = layoutGraph(createStarterGraph(graphs));
        const nextGraphs = [...graphs, nextGraph];
        setSelectedGraphId(nextGraph.id);
        setSelection({ kind: 'graph' });
        applyStudio(nextGraphs, statePresets, { defaultGraphId: resolvedDefaultGraphId || nextGraph.id });
    }, [applyStudio, graphs, resolvedDefaultGraphId, statePresets]);

    const duplicateGraph = useCallback(() => {
        if (!currentGraph) {
            return;
        }
        const nextGraph = cloneJson(currentGraph);
        nextGraph.id = makeUniqueId(`${currentGraph.id}-copy`, graphs.map((graph) => graph.id), 'graph');
        nextGraph.name = `${currentGraph.name} Copy`;
        const nextGraphs = [...graphs, nextGraph];
        setSelectedGraphId(nextGraph.id);
        setSelection({ kind: 'graph' });
        applyStudio(nextGraphs, statePresets, { defaultGraphId: resolvedDefaultGraphId });
    }, [applyStudio, currentGraph, graphs, resolvedDefaultGraphId, statePresets]);

    const deleteGraph = useCallback(() => {
        if (!currentGraph) {
            return;
        }
        if (!window.confirm(`Delete graph "${currentGraph.name}"?`)) {
            return;
        }
        const remaining = graphs.filter((graph) => graph.id !== currentGraph.id);
        const nextGraphs = remaining.length ? remaining : [layoutGraph(createStarterGraph([]))];
        const nextDefaultGraphId = ensureDefaultGraphId(
            nextGraphs,
            resolvedDefaultGraphId === currentGraph.id ? nextGraphs[0]?.id : resolvedDefaultGraphId
        );
        setSelectedGraphId(nextGraphs[0]?.id || null);
        setSelection({ kind: 'graph' });
        applyStudio(nextGraphs, statePresets, { defaultGraphId: nextDefaultGraphId });
    }, [applyStudio, currentGraph, graphs, resolvedDefaultGraphId, statePresets]);

    const addNode = useCallback(
        (nodeType: GraphNodeType) => {
            if (!currentGraph) {
                return;
            }
            const existingIds = currentGraph.nodes.map((node) => node.id);
            const nextId = makeUniqueId(
                nodeType === 'react_agent' ? 'react' : nodeType === 'tool_call' ? 'tool' : 'router',
                existingIds,
                'node'
            );
            const nextNode: GraphNode = {
                id: nextId,
                type: nodeType,
                name:
                    nodeType === 'react_agent'
                        ? 'Agent Node'
                        : nodeType === 'tool_call'
                            ? 'Tool Node'
                            : 'Router Node',
                ...(nodeType === 'react_agent' ? { input_template: '{{state.input.user_message}}' } : {}),
                ...(nodeType === 'tool_call' ? { tool_name: sortedTools[0]?.name || '' } : {}),
                ui: { position: getNextNodePosition(currentGraph) },
            };
            updateCurrentGraph((graph) => ({
                ...graph,
                nodes: [...graph.nodes, nextNode],
            }));
            setSelection({ kind: 'node', nodeId: nextId });
        },
        [currentGraph, sortedTools, updateCurrentGraph]
    );

    const updateGraphField = useCallback(
        (field: keyof GraphDefinition, value: unknown) => {
            if (!currentGraph) {
                return;
            }
            if (field === 'id') {
                const nextId = String(value || '').trim();
                if (!nextId || nextId === currentGraph.id) {
                    return;
                }
                const uniqueId = makeUniqueId(
                    nextId,
                    graphs.filter((graph) => graph.id !== currentGraph.id).map((graph) => graph.id),
                    'graph'
                );
                const nextGraphs = graphs.map((graph) => (graph.id === currentGraph.id ? { ...graph, id: uniqueId } : graph));
                const nextDefaultGraphId = resolvedDefaultGraphId === currentGraph.id ? uniqueId : resolvedDefaultGraphId;
                setSelectedGraphId(uniqueId);
                applyStudio(nextGraphs, statePresets, { defaultGraphId: nextDefaultGraphId });
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                [field]: value,
            }));
        },
        [applyStudio, currentGraph, graphs, resolvedDefaultGraphId, statePresets, updateCurrentGraph]
    );

    const updateNodeField = useCallback(
        (nodeId: string, field: keyof GraphNode, value: unknown) => {
            if (!currentGraph) {
                return;
            }
            if (field === 'id') {
                const nextId = String(value || '').trim();
                if (!nextId || nextId === nodeId) {
                    return;
                }
                const existingIds = currentGraph.nodes.filter((node) => node.id !== nodeId).map((node) => node.id);
                const uniqueId = makeUniqueId(nextId, existingIds, 'node');
                updateCurrentGraph((graph) => ({
                    ...graph,
                    nodes: graph.nodes.map((node) => (node.id === nodeId ? { ...node, id: uniqueId } : node)),
                    edges: graph.edges.map((edge) => ({
                        ...edge,
                        source: edge.source === nodeId ? uniqueId : edge.source,
                        target: edge.target === nodeId ? uniqueId : edge.target,
                    })),
                }));
                setSelection({ kind: 'node', nodeId: uniqueId });
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                nodes: graph.nodes.map((node) => (node.id === nodeId ? { ...node, [field]: value } : node)),
            }));
        },
        [currentGraph, updateCurrentGraph]
    );

    const updateEdgeField = useCallback(
        (edgeId: string, field: keyof GraphEdge, value: unknown) => {
            if (!currentGraph) {
                return;
            }
            if (field === 'id') {
                const nextId = String(value || '').trim();
                if (!nextId || nextId === edgeId) {
                    return;
                }
                const existingIds = currentGraph.edges
                    .map((edge) => edge.id)
                    .filter((candidate): candidate is string => Boolean(candidate && candidate !== edgeId));
                const uniqueId = makeUniqueId(nextId, existingIds, 'edge');
                updateCurrentGraph((graph) => ({
                    ...graph,
                    edges: graph.edges.map((edge) => (edge.id === edgeId ? { ...edge, id: uniqueId } : edge)),
                }));
                setSelection({ kind: 'edge', edgeId: uniqueId });
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                edges: graph.edges.map((edge) => (edge.id === edgeId ? { ...edge, [field]: value } : edge)),
            }));
        },
        [currentGraph, updateCurrentGraph]
    );

    const addStatePreset = useCallback(() => {
        const nextPreset = createStarterStatePreset(statePresets);
        const nextStatePresets = [...statePresets, nextPreset];
        updateStatePresets(nextStatePresets);
        setSelection({ kind: 'statePreset', presetId: nextPreset.id });
    }, [statePresets, updateStatePresets]);

    const updateStatePresetField = useCallback(
        (presetId: string, field: keyof StatePreset, value: unknown) => {
            const currentPreset = findStatePreset(statePresets, presetId);
            if (!currentPreset) {
                return;
            }
            if (field === 'id') {
                const nextId = String(value || '').trim();
                if (!nextId || nextId === presetId) {
                    return;
                }
                const uniqueId = makeUniqueId(
                    nextId,
                    statePresets.filter((preset) => preset.id !== presetId).map((preset) => preset.id),
                    'state'
                );
                const nextStatePresets = statePresets.map((preset) =>
                    preset.id === presetId ? { ...preset, id: uniqueId } : preset
                );
                const nextGraphs = graphs.map((graph) =>
                    graph.state_preset_id === presetId ? { ...graph, state_preset_id: uniqueId } : graph
                );
                updateStatePresets(nextStatePresets, nextGraphs);
                setSelection({ kind: 'statePreset', presetId: uniqueId });
                return;
            }

            const nextStatePresets = statePresets.map((preset) =>
                preset.id === presetId ? { ...preset, [field]: value } : preset
            );
            const nextPreset =
                (nextStatePresets.find((preset) => preset.id === presetId) as StatePreset | undefined) || currentPreset;
            const nextGraphs =
                field === 'state'
                    ? syncGraphsToPresetSnapshot(graphs, presetId, nextPreset.state, nextPreset.state_schema)
                    : field === 'state_schema'
                        ? syncGraphsToPresetSnapshot(graphs, presetId, nextPreset.state, nextPreset.state_schema)
                    : graphs;
            updateStatePresets(nextStatePresets, nextGraphs);
        },
        [graphs, statePresets, updateStatePresets]
    );

    const deleteSelectedStatePreset = useCallback(() => {
        if (!selectedPreset) {
            return;
        }
        const usageCount = getPresetUsageCount(graphs, selectedPreset.id);
        const usageText = usageCount
            ? `\n\n${usageCount} graph(s) currently use it and will be converted to custom state.`
            : '';
        if (!window.confirm(`Delete state preset "${selectedPreset.name}"?${usageText}`)) {
            return;
        }
        const nextStatePresets = statePresets.filter((preset) => preset.id !== selectedPreset.id);
        const nextGraphs = graphs.map((graph) =>
            graph.state_preset_id === selectedPreset.id
                ? {
                      ...graph,
                      state_preset_id: undefined,
                      initial_state: cloneJson(selectedPreset.state ?? {}),
                      state_schema: normalizeStateSchema(selectedPreset.state_schema, selectedPreset.state),
                  }
                : graph
        );
        updateStatePresets(nextStatePresets, nextGraphs);
        setSelection({ kind: 'graph' });
    }, [graphs, selectedPreset, statePresets, updateStatePresets]);

    const deleteSelectedNode = useCallback(() => {
        if (!currentNode || !window.confirm(`Delete node "${currentNode.name || currentNode.id}" and its connected edges?`)) {
            return;
        }
        updateCurrentGraph((graph) => ({
            ...graph,
            nodes: graph.nodes.filter((node) => node.id !== currentNode.id),
            edges: graph.edges.filter((edge) => edge.source !== currentNode.id && edge.target !== currentNode.id),
        }));
        setSelection({ kind: 'graph' });
    }, [currentNode, updateCurrentGraph]);

    const deleteSelectedEdge = useCallback(() => {
        if (!currentEdge || !window.confirm(`Delete edge "${currentEdge.id || `${currentEdge.source} -> ${currentEdge.target}`}"?`)) {
            return;
        }
        updateCurrentGraph((graph) => ({
            ...graph,
            edges: graph.edges.filter((edge) => edge.id !== currentEdge.id),
        }));
        setSelection({ kind: 'graph' });
    }, [currentEdge, updateCurrentGraph]);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key !== 'Delete' || event.defaultPrevented || event.repeat) {
                return;
            }
            if (event.altKey || event.ctrlKey || event.metaKey) {
                return;
            }
            if (isEditableShortcutTarget(event.target)) {
                return;
            }
            if (selection.kind === 'node') {
                event.preventDefault();
                deleteSelectedNode();
                return;
            }
            if (selection.kind === 'edge') {
                event.preventDefault();
                deleteSelectedEdge();
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [deleteSelectedEdge, deleteSelectedNode, selection]);

    const handleConnect = useCallback(
        (connection: Connection) => {
            if (!currentGraph || !connection.source || !connection.target) {
                return;
            }
            if (connection.source === GRAPH_END || connection.target === GRAPH_START) {
                return;
            }
            if (connection.source === GRAPH_START && connection.target === GRAPH_END) {
                return;
            }
            const nextId = makeUniqueId(
                `${connection.source}_to_${connection.target}`,
                currentGraph.edges.map((edge) => edge.id || ''),
                'edge'
            );
            updateCurrentGraph((graph) => ({
                ...graph,
                edges: [
                    ...graph.edges,
                    {
                        id: nextId,
                        source: connection.source!,
                        target: connection.target!,
                        priority: 0,
                    },
                ],
            }));
            setSelection({ kind: 'edge', edgeId: nextId });
        },
        [currentGraph, updateCurrentGraph]
    );

    const syncNodePosition = useCallback(
        (nodeId: string, position: { x: number; y: number }) => {
            if (nodeId === GRAPH_START || nodeId === GRAPH_END) {
                return;
            }
            updateCurrentGraph((graph) => {
                let changed = false;
                const nextNodes = graph.nodes.map((graphNode) => {
                    if (graphNode.id !== nodeId) {
                        return graphNode;
                    }
                    const previousPosition = graphNode.ui?.position;
                    if (previousPosition?.x === position.x && previousPosition?.y === position.y) {
                        return graphNode;
                    }
                    changed = true;
                    return {
                        ...graphNode,
                        ui: {
                            ...(graphNode.ui || {}),
                            position: {
                                x: position.x,
                                y: position.y,
                            },
                        },
                    };
                });
                return changed
                    ? {
                          ...graph,
                          nodes: nextNodes,
                      }
                    : graph;
            });
        },
        [updateCurrentGraph]
    );

    const handleNodeDrag = useCallback<OnNodeDrag<Node<GraphCanvasNodeData>>>(
        (_event, node) => {
            syncNodePosition(node.id, node.position);
        },
        [syncNodePosition]
    );

    const handleNodeDragStop = useCallback<OnNodeDrag<Node<GraphCanvasNodeData>>>(
        (_event, node) => {
            syncNodePosition(node.id, node.position);
        },
        [syncNodePosition]
    );

    const handleMoveEnd = useCallback<OnMoveEnd>(
        (_event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
            updateCurrentGraph((graph) => ({
                ...graph,
                ui: {
                    ...(graph.ui || {}),
                    viewport: {
                        x: viewport.x,
                        y: viewport.y,
                        zoom: viewport.zoom,
                    },
                },
            }));
        },
        [updateCurrentGraph]
    );

    const handleInitialStateChange = useCallback(
        (text: string) => {
            setGraphStateText(text);
            if (!currentGraph) {
                return;
            }
            try {
                const parsed = text.trim() ? JSON.parse(text) : {};
                const reservedError = getReservedStateValueError(parsed);
                if (reservedError) {
                    setGraphStateError(reservedError);
                    return;
                }
                setGraphStateError(null);
                updateCurrentGraph((graph) => ({
                    ...graph,
                    initial_state: parsed,
                    state_schema: normalizeStateSchema(graph.state_schema, parsed),
                }));
            } catch (error) {
                setGraphStateError(error instanceof Error ? error.message : 'Invalid JSON');
            }
        },
        [currentGraph, updateCurrentGraph]
    );

    const handlePresetStateChange = useCallback(
        (text: string) => {
            setPresetStateText(text);
            if (!selectedPreset) {
                return;
            }
            try {
                const parsed = text.trim() ? JSON.parse(text) : {};
                const reservedError = getReservedStateValueError(parsed);
                if (reservedError) {
                    setPresetStateError(reservedError);
                    return;
                }
                setPresetStateError(null);
                updateStatePresetField(selectedPreset.id, 'state', parsed);
                updateStatePresetField(
                    selectedPreset.id,
                    'state_schema',
                    normalizeStateSchema(selectedPreset.state_schema, parsed)
                );
            } catch (error) {
                setPresetStateError(error instanceof Error ? error.message : 'Invalid JSON');
            }
        },
        [selectedPreset, updateStatePresetField]
    );

    const addCustomGraphField = useCallback(() => {
        if (!currentGraph) {
            return;
        }
        const path = normalizeStatePath(graphFieldDraft);
        if (!path) {
            return;
        }
        if (isReservedStatePath(path)) {
            setGraphStateError('Reserved runtime paths `input.*` and `messages` cannot be added to custom state.');
            return;
        }
        if (hasStateField(currentGraph.initial_state, path)) {
            setGraphFieldDraft('');
            return;
        }
        const nextState = setStateField(
            currentGraph.initial_state,
            path,
            getDefaultStateValueForType(graphFieldTypeDraft)
        );
        const nextSchema = normalizeStateSchema(
            [...(currentGraph.state_schema || []), { path, type: graphFieldTypeDraft, mutable: false }],
            nextState
        );
        setGraphStateText(formatGraphState(nextState));
        setGraphStateError(null);
        updateCurrentGraph((graph) => ({
            ...graph,
            initial_state: nextState,
            state_schema: nextSchema,
        }));
        setGraphFieldDraft('');
        setGraphFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
    }, [currentGraph, graphFieldDraft, graphFieldTypeDraft, updateCurrentGraph]);

    const removeCustomGraphField = useCallback(
        (path: string) => {
            if (!currentGraph) {
                return;
            }
            const nextState = removeStateField(currentGraph.initial_state, path);
            const nextSchema = normalizeStateSchema(
                (currentGraph.state_schema || []).filter((field) => field.path !== path),
                nextState
            );
            setGraphStateText(formatGraphState(nextState));
            setGraphStateError(null);
            updateCurrentGraph((graph) => ({
                ...graph,
                initial_state: nextState,
                state_schema: nextSchema,
            }));
        },
        [currentGraph, updateCurrentGraph]
    );

    const addPresetField = useCallback(() => {
        if (!selectedPreset) {
            return;
        }
        const path = normalizeStatePath(presetFieldDraft);
        if (!path) {
            return;
        }
        if (isReservedStatePath(path)) {
            setPresetStateError('Reserved runtime paths `input.*` and `messages` cannot be added to preset state.');
            return;
        }
        if (hasStateField(selectedPreset.state, path)) {
            setPresetFieldDraft('');
            return;
        }
        const nextState = setStateField(
            selectedPreset.state,
            path,
            getDefaultStateValueForType(presetFieldTypeDraft)
        );
        const nextSchema = normalizeStateSchema(
            [...(selectedPreset.state_schema || []), { path, type: presetFieldTypeDraft, mutable: false }],
            nextState
        );
        setPresetStateText(formatGraphState(nextState));
        setPresetStateError(null);
        updateStatePresetField(selectedPreset.id, 'state', nextState);
        updateStatePresetField(selectedPreset.id, 'state_schema', nextSchema);
        setPresetFieldDraft('');
        setPresetFieldTypeDraft(DEFAULT_STATE_FIELD_TYPE);
    }, [presetFieldTypeDraft, presetFieldDraft, selectedPreset, updateStatePresetField]);

    const removePresetField = useCallback(
        (path: string) => {
            if (!selectedPreset) {
                return;
            }
            const nextState = removeStateField(selectedPreset.state, path);
            const nextSchema = normalizeStateSchema(
                (selectedPreset.state_schema || []).filter((field) => field.path !== path),
                nextState
            );
            setPresetStateText(formatGraphState(nextState));
            setPresetStateError(null);
            updateStatePresetField(selectedPreset.id, 'state', nextState);
            updateStatePresetField(selectedPreset.id, 'state_schema', nextSchema);
        },
        [selectedPreset, updateStatePresetField]
    );

    const updateGraphFieldType = useCallback(
        (path: string, type: StateFieldType) => {
            if (!currentGraph) {
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                state_schema: updateStateFieldType(graph.state_schema, graph.initial_state, path, type),
            }));
        },
        [currentGraph, updateCurrentGraph]
    );

    const updatePresetFieldType = useCallback(
        (path: string, type: StateFieldType) => {
            if (!selectedPreset) {
                return;
            }
            updateStatePresetField(
                selectedPreset.id,
                'state_schema',
                updateStateFieldType(selectedPreset.state_schema, selectedPreset.state, path, type)
            );
        },
        [selectedPreset, updateStatePresetField]
    );

    const updateGraphFieldMutable = useCallback(
        (path: string, mutable: boolean) => {
            if (!currentGraph) {
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                state_schema: updateStateFieldMutable(graph.state_schema, graph.initial_state, path, mutable),
            }));
        },
        [currentGraph, updateCurrentGraph]
    );

    const updatePresetFieldMutable = useCallback(
        (path: string, mutable: boolean) => {
            if (!selectedPreset) {
                return;
            }
            updateStatePresetField(
                selectedPreset.id,
                'state_schema',
                updateStateFieldMutable(selectedPreset.state_schema, selectedPreset.state, path, mutable)
            );
        },
        [selectedPreset, updateStatePresetField]
    );

    const setGraphStateSource = useCallback(
        (source: 'custom' | 'preset') => {
            if (!currentGraph) {
                return;
            }
            if (source === 'custom') {
                const nextState = cloneJson(effectiveGraphState);
                setGraphStateText(formatGraphState(nextState));
                setGraphStateError(null);
                updateCurrentGraph((graph) => ({
                    ...graph,
                    state_preset_id: undefined,
                    initial_state: nextState,
                    state_schema: normalizeStateSchema(effectiveGraphStateSchema, nextState),
                }));
                return;
            }
            if (!statePresets.length) {
                return;
            }
            const nextPreset = linkedPreset || statePresets[0];
            updateCurrentGraph((graph) => ({
                ...graph,
                state_preset_id: nextPreset.id,
                initial_state: cloneJson(nextPreset.state ?? {}),
                state_schema: normalizeStateSchema(nextPreset.state_schema, nextPreset.state),
            }));
        },
        [currentGraph, effectiveGraphState, effectiveGraphStateSchema, linkedPreset, statePresets, updateCurrentGraph]
    );

    const selectGraphPreset = useCallback(
        (presetId: string) => {
            const preset = findStatePreset(statePresets, presetId);
            if (!preset) {
                return;
            }
            updateCurrentGraph((graph) => ({
                ...graph,
                state_preset_id: preset.id,
                initial_state: cloneJson(preset.state ?? {}),
                state_schema: normalizeStateSchema(preset.state_schema, preset.state),
            }));
        },
        [statePresets, updateCurrentGraph]
    );

    const copyPresetToCustomGraphState = useCallback(() => {
        if (!currentGraph || !linkedPreset) {
            return;
        }
        const nextState = cloneJson(linkedPreset.state ?? {});
        setGraphStateText(formatGraphState(nextState));
        setGraphStateError(null);
        updateCurrentGraph((graph) => ({
            ...graph,
            state_preset_id: undefined,
            initial_state: nextState,
            state_schema: normalizeStateSchema(linkedPreset.state_schema, linkedPreset.state),
        }));
    }, [currentGraph, linkedPreset, updateCurrentGraph]);

    if (!graphs.length) {
        return (
            <div className="graph-editor-shell">
                <div className="graph-editor-empty">
                    <h4>No graphs configured</h4>
                    <button type="button" className="add-btn add-inline" onClick={addGraph}>
                        + Create Graph
                    </button>
                </div>
            </div>
        );
    }

    const graphStateSource = currentGraph?.state_preset_id ? 'preset' : 'custom';

    return (
        <div className="graph-editor-shell">
            <div className="graph-editor-header">
                <div>
                    <div className="graph-editor-kicker">Graph Studio</div>
                    <h4>Visual Graph Configuration</h4>
                </div>
                <div className="graph-editor-header-actions">
                    <button
                        type="button"
                        className="add-btn add-inline"
                        onClick={() => setShowAdvancedJson((prev) => !prev)}
                    >
                        {showAdvancedJson ? '隐藏高级 JSON' : '显示高级 JSON'}
                    </button>
                </div>
            </div>

            <div className="graph-editor-layout">
                <aside className="graph-editor-sidebar">
                    <div className="graph-editor-panel">
                        <div className="graph-editor-panel-title-row">
                            <h5>Graphs</h5>
                            <button type="button" className="add-btn add-inline" onClick={addGraph}>
                                + Add
                            </button>
                        </div>
                        <div className="form-group">
                            <label>Default Graph</label>
                            <select
                                value={resolvedDefaultGraphId}
                                onChange={(event) =>
                                    applyStudio(graphs, statePresets, { defaultGraphId: event.target.value })
                                }
                            >
                                {graphs.map((graph) => (
                                    <option key={graph.id} value={graph.id}>
                                        {graph.name || graph.id}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="graph-editor-graph-list">
                            {graphs.map((graph) => {
                                const active = graph.id === currentGraph?.id;
                                const isDefault = graph.id === resolvedDefaultGraphId;
                                const sourcePreset = findStatePreset(statePresets, graph.state_preset_id);
                                return (
                                    <button
                                        key={graph.id}
                                        type="button"
                                        className={`graph-editor-graph-card${active ? ' active' : ''}`}
                                        onClick={() => {
                                            setSelectedGraphId(graph.id);
                                            setSelection({ kind: 'graph' });
                                        }}
                                    >
                                        <div className="graph-editor-graph-card-header">
                                            <span>{graph.name || graph.id}</span>
                                            {isDefault && <span className="graph-editor-badge">default</span>}
                                        </div>
                                        <div className="graph-editor-graph-card-meta">
                                            <span>{graph.nodes.length} nodes</span>
                                            <span>{graph.edges.length} edges</span>
                                        </div>
                                        <div className="graph-editor-graph-card-state">
                                            {sourcePreset ? `preset: ${sourcePreset.name}` : 'custom state'}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                        <div className="graph-editor-inline-actions">
                            <button
                                type="button"
                                className="add-btn add-inline"
                                onClick={duplicateGraph}
                                disabled={!currentGraph}
                            >
                                Duplicate
                            </button>
                            <button
                                type="button"
                                className="add-btn add-inline danger"
                                onClick={deleteGraph}
                                disabled={!currentGraph}
                            >
                                Delete
                            </button>
                        </div>
                    </div>

                    <div className="graph-editor-panel">
                        <div className="graph-editor-panel-title-row">
                            <h5>State Library</h5>
                            <button type="button" className="add-btn add-inline" onClick={addStatePreset}>
                                + Add
                            </button>
                        </div>
                        <div className="graph-editor-state-library-list">
                            {statePresets.length ? (
                                statePresets.map((preset) => {
                                    const active = selection.kind === 'statePreset' && selection.presetId === preset.id;
                                    const usageCount = getPresetUsageCount(graphs, preset.id);
                                    return (
                                        <button
                                            key={preset.id}
                                            type="button"
                                            className={`graph-editor-state-card${active ? ' active' : ''}`}
                                            onClick={() => setSelection({ kind: 'statePreset', presetId: preset.id })}
                                        >
                                            <div className="graph-editor-state-card-header">
                                                <span>{preset.name || preset.id}</span>
                                                <span className="graph-editor-chip state">
                                                    {usageCount} graph{usageCount === 1 ? '' : 's'}
                                                </span>
                                            </div>
                                            <div className="graph-editor-state-card-meta">{preset.id}</div>
                                        </button>
                                    );
                                })
                            ) : (
                                <div className="graph-editor-state-empty">
                                    No presets yet. Create one to reuse graph state definitions.
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="graph-editor-panel">
                        <div className="graph-editor-panel-title-row">
                            <h5>Add Node</h5>
                        </div>
                        <div className="graph-editor-node-actions">
                            <button
                                type="button"
                                className="graph-editor-node-action react"
                                onClick={() => addNode('react_agent')}
                            >
                                <span>ReAct Agent</span>
                                <small>LLM + tools</small>
                            </button>
                            <button
                                type="button"
                                className="graph-editor-node-action tool"
                                onClick={() => addNode('tool_call')}
                            >
                                <span>Tool Call</span>
                                <small>Direct tool execution</small>
                            </button>
                            <button
                                type="button"
                                className="graph-editor-node-action router"
                                onClick={() => addNode('router')}
                            >
                                <span>Router</span>
                                <small>Branch decision point</small>
                            </button>
                        </div>
                    </div>
                </aside>

                <section className="graph-editor-canvas-panel">
                    <div className="graph-editor-canvas-header">
                        <div>
                            <h5>{currentGraph?.name || 'Graph Canvas'}</h5>
                            <p>Drag nodes, connect handles, zoom the canvas, and edit details in the inspector.</p>
                        </div>
                    </div>
                    <div className="graph-editor-canvas">
                        {currentGraph && (
                            <ReactFlow
                                key={currentGraph.id}
                                nodes={canvasNodes}
                                edges={canvasEdges}
                                nodeTypes={nodeTypes}
                                defaultViewport={currentGraph.ui?.viewport || DEFAULT_VIEWPORT}
                                colorMode="dark"
                                fitView={false}
                                minZoom={0.35}
                                maxZoom={1.9}
                                onConnect={handleConnect}
                                onPaneClick={() => setSelection({ kind: 'graph' })}
                                onNodeClick={(_event, node) => {
                                    if (node.id === GRAPH_START || node.id === GRAPH_END) {
                                        setSelection({ kind: 'graph' });
                                        return;
                                    }
                                    setSelection({ kind: 'node', nodeId: node.id });
                                }}
                                onEdgeClick={(_event, edge) => setSelection({ kind: 'edge', edgeId: edge.id })}
                                onNodeDrag={handleNodeDrag}
                                onNodeDragStop={handleNodeDragStop}
                                onMoveEnd={handleMoveEnd}
                            >
                                <MiniMap
                                    pannable
                                    zoomable
                                    nodeStrokeWidth={3}
                                    nodeColor={(node) => {
                                        if (node.id === GRAPH_START) return '#22c55e';
                                        if (node.id === GRAPH_END) return '#f43f5e';
                                        const item = currentGraph.nodes.find((graphNode) => graphNode.id === node.id);
                                        if (item?.type === 'tool_call') return '#0ea5e9';
                                        if (item?.type === 'router') return '#f59e0b';
                                        return '#8b5cf6';
                                    }}
                                />
                                <Controls showInteractive={false} />
                                <Background
                                    gap={22}
                                    size={1}
                                    variant={BackgroundVariant.Dots}
                                    color="rgba(255, 245, 157, 0.14)"
                                />
                            </ReactFlow>
                        )}
                    </div>
                </section>

                <aside className="graph-editor-inspector">
                    <div className="graph-editor-panel">
                        <div className="graph-editor-panel-title-row">
                            <h5>
                                {selection.kind === 'edge'
                                    ? 'Edge Inspector'
                                    : selection.kind === 'node'
                                        ? 'Node Inspector'
                                        : selection.kind === 'statePreset'
                                            ? 'State Preset Inspector'
                                            : 'Graph Inspector'}
                            </h5>
                        </div>

                        {selection.kind === 'graph' && currentGraph && (
                            <div className="graph-editor-inspector-body">
                                <div className="form-group">
                                    <label>Graph ID</label>
                                    <input
                                        type="text"
                                        value={currentGraph.id}
                                        onChange={(event) => updateGraphField('id', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Name</label>
                                    <input
                                        type="text"
                                        value={currentGraph.name}
                                        onChange={(event) => updateGraphField('name', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Max Hops</label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={10000}
                                        value={currentGraph.max_hops ?? 100}
                                        onChange={(event) =>
                                            updateGraphField(
                                                'max_hops',
                                                Math.max(1, Number.parseInt(event.target.value || '100', 10) || 100)
                                            )
                                        }
                                    />
                                </div>
                                <div className="form-group">
                                    <label>State Source</label>
                                    <div className="graph-editor-state-source-tabs">
                                        <button
                                            type="button"
                                            className={graphStateSource === 'custom' ? 'active' : ''}
                                            onClick={() => setGraphStateSource('custom')}
                                        >
                                            Custom
                                        </button>
                                        <button
                                            type="button"
                                            className={graphStateSource === 'preset' ? 'active' : ''}
                                            onClick={() => setGraphStateSource('preset')}
                                            disabled={!statePresets.length}
                                        >
                                            Preset
                                        </button>
                                    </div>
                                </div>

                                {graphStateSource === 'preset' ? (
                                    <div className="graph-editor-state-preview">
                                        <div className="form-group">
                                            <label>Linked Preset</label>
                                            <select
                                                value={currentGraph.state_preset_id || ''}
                                                onChange={(event) => selectGraphPreset(event.target.value)}
                                            >
                                                {statePresets.map((preset) => (
                                                    <option key={preset.id} value={preset.id}>
                                                        {preset.name || preset.id}
                                                    </option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="graph-editor-inline-actions">
                                            <button
                                                type="button"
                                                className="add-btn add-inline"
                                                onClick={() =>
                                                    linkedPreset &&
                                                    setSelection({ kind: 'statePreset', presetId: linkedPreset.id })
                                                }
                                                disabled={!linkedPreset}
                                            >
                                                Open Preset
                                            </button>
                                            <button
                                                type="button"
                                                className="add-btn add-inline"
                                                onClick={copyPresetToCustomGraphState}
                                                disabled={!linkedPreset}
                                            >
                                                Copy To Custom
                                            </button>
                                        </div>
                                        <StateFieldEditor
                                            stateValue={effectiveGraphState}
                                            stateSchema={effectiveGraphStateSchema}
                                            readOnly
                                            emptyMessage="This preset is empty."
                                        />
                                    </div>
                                ) : (
                                    <StateFieldEditor
                                        stateValue={currentGraph.initial_state}
                                        stateSchema={currentGraph.state_schema}
                                        addFieldDraft={graphFieldDraft}
                                        addFieldType={graphFieldTypeDraft}
                                        onAddFieldDraftChange={setGraphFieldDraft}
                                        onAddFieldTypeChange={setGraphFieldTypeDraft}
                                        onAddField={addCustomGraphField}
                                        onFieldTypeChange={updateGraphFieldType}
                                        onFieldMutableChange={updateGraphFieldMutable}
                                        onRemoveField={removeCustomGraphField}
                                        showAdvancedJson={showGraphStateAdvancedJson}
                                        onToggleAdvancedJson={() =>
                                            setShowGraphStateAdvancedJson((prev) => !prev)
                                        }
                                        jsonText={graphStateText}
                                        jsonError={graphStateError}
                                        onJsonChange={handleInitialStateChange}
                                        emptyMessage="No custom state fields yet."
                                        advancedLabel="State JSON"
                                    />
                                )}
                            </div>
                        )}

                        {selection.kind === 'statePreset' && selectedPreset && (
                            <div className="graph-editor-inspector-body">
                                <div className="graph-editor-inspector-meta">
                                    <span className="graph-editor-chip state">
                                        {getPresetUsageCount(graphs, selectedPreset.id)} linked graph
                                        {getPresetUsageCount(graphs, selectedPreset.id) === 1 ? '' : 's'}
                                    </span>
                                    <button
                                        type="button"
                                        className="add-btn add-inline danger"
                                        onClick={deleteSelectedStatePreset}
                                    >
                                        Delete Preset
                                    </button>
                                </div>
                                <div className="form-group">
                                    <label>Preset ID</label>
                                    <input
                                        type="text"
                                        value={selectedPreset.id}
                                        onChange={(event) =>
                                            updateStatePresetField(selectedPreset.id, 'id', event.target.value)
                                        }
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Name</label>
                                    <input
                                        type="text"
                                        value={selectedPreset.name}
                                        onChange={(event) =>
                                            updateStatePresetField(selectedPreset.id, 'name', event.target.value)
                                        }
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Description</label>
                                    <textarea
                                        rows={3}
                                        value={selectedPreset.description || ''}
                                        onChange={(event) =>
                                            updateStatePresetField(
                                                selectedPreset.id,
                                                'description',
                                                event.target.value || undefined
                                            )
                                        }
                                    />
                                </div>
                                <StateFieldEditor
                                    stateValue={selectedPreset.state}
                                    stateSchema={selectedPreset.state_schema}
                                    addFieldDraft={presetFieldDraft}
                                    addFieldType={presetFieldTypeDraft}
                                    onAddFieldDraftChange={setPresetFieldDraft}
                                    onAddFieldTypeChange={setPresetFieldTypeDraft}
                                    onAddField={addPresetField}
                                    onFieldTypeChange={updatePresetFieldType}
                                    onFieldMutableChange={updatePresetFieldMutable}
                                    onRemoveField={removePresetField}
                                    showAdvancedJson={showPresetStateAdvancedJson}
                                    onToggleAdvancedJson={() =>
                                        setShowPresetStateAdvancedJson((prev) => !prev)
                                    }
                                    jsonText={presetStateText}
                                    jsonError={presetStateError}
                                    onJsonChange={handlePresetStateChange}
                                    emptyMessage="No preset fields yet."
                                    advancedLabel="Preset JSON"
                                />
                            </div>
                        )}

                        {selection.kind === 'node' && currentNode && (
                            <div className="graph-editor-inspector-body">
                                <div className="graph-editor-inspector-meta">
                                    <span className={`graph-editor-chip ${currentNode.type}`}>{currentNode.type}</span>
                                    <button
                                        type="button"
                                        className="add-btn add-inline danger"
                                        onClick={deleteSelectedNode}
                                    >
                                        Delete Node
                                    </button>
                                </div>
                                <div className="form-group">
                                    <label>Node ID</label>
                                    <input
                                        type="text"
                                        value={currentNode.id}
                                        onChange={(event) => updateNodeField(currentNode.id, 'id', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Name</label>
                                    <input
                                        type="text"
                                        value={currentNode.name || ''}
                                        onChange={(event) => updateNodeField(currentNode.id, 'name', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Description</label>
                                    <textarea
                                        rows={3}
                                        value={currentNode.description || ''}
                                        onChange={(event) => updateNodeField(currentNode.id, 'description', event.target.value)}
                                    />
                                </div>

                                {currentNode.type === 'react_agent' && (
                                    <>
                                        <div className="form-group">
                                            <label>Profile</label>
                                            <select
                                                value={currentNode.profile_id || ''}
                                                onChange={(event) =>
                                                    updateNodeField(currentNode.id, 'profile_id', event.target.value || undefined)
                                                }
                                            >
                                                <option value="">Follow session/default</option>
                                                {sortedProfiles.map((profile) => (
                                                    <option key={profile.id} value={profile.id}>
                                                        {profile.name || profile.id}
                                                    </option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="form-group">
                                            <label>Max Iterations</label>
                                            <input
                                                type="number"
                                                min={1}
                                                value={currentNode.max_iterations ?? ''}
                                                onChange={(event) =>
                                                    updateNodeField(
                                                        currentNode.id,
                                                        'max_iterations',
                                                        event.target.value
                                                            ? Math.max(1, Number.parseInt(event.target.value, 10) || 1)
                                                            : undefined
                                                    )
                                                }
                                            />
                                        </div>
                                        <div className="form-group">
                                            <label>Input Template</label>
                                            <textarea
                                                rows={4}
                                                value={formatFlexibleValue(currentNode.input_template)}
                                                onChange={(event) =>
                                                    updateNodeField(
                                                        currentNode.id,
                                                        'input_template',
                                                        parseFlexibleValue(event.target.value)
                                                    )
                                                }
                                            />
                                            {currentNodeInputTemplateError ? (
                                                <div className="form-error">{currentNodeInputTemplateError}</div>
                                            ) : (
                                                <small>
                                                    Leave blank to fall back to {'{{state.input.user_message}}'}. {'{{input}}'}
                                                    {' '}and {'{{request.*}}'} are invalid in graph mode.
                                                </small>
                                            )}
                                        </div>
                                        <div className="form-group">
                                            <label>Output Path</label>
                                            <input
                                                type="text"
                                                value={currentNode.output_path || ''}
                                                onChange={(event) => updateNodeField(currentNode.id, 'output_path', event.target.value)}
                                            />
                                            {currentNodeOutputPathError ? (
                                                <div className="form-error">{currentNodeOutputPathError}</div>
                                            ) : (
                                                <small>Write node output to a custom state path outside `input.*` and `messages`.</small>
                                            )}
                                        </div>
                                    </>
                                )}

                                {currentNode.type === 'tool_call' && (
                                    <>
                                        <div className="form-group">
                                            <label>Tool</label>
                                            <select
                                                value={currentNode.tool_name || ''}
                                                onChange={(event) => updateNodeField(currentNode.id, 'tool_name', event.target.value)}
                                            >
                                                <option value="">Select a tool</option>
                                                {sortedTools.map((tool) => (
                                                    <option key={tool.name} value={tool.name}>
                                                        {tool.name}
                                                    </option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="form-group">
                                            <label>Args Template</label>
                                            <textarea
                                                rows={5}
                                                value={formatFlexibleValue(currentNode.args_template)}
                                                onChange={(event) =>
                                                    updateNodeField(
                                                        currentNode.id,
                                                        'args_template',
                                                        parseFlexibleValue(event.target.value)
                                                    )
                                                }
                                            />
                                            {currentNodeArgsTemplateError ? (
                                                <div className="form-error">{currentNodeArgsTemplateError}</div>
                                            ) : (
                                                <small>Only `state`, `result`, and `session` template roots are allowed.</small>
                                            )}
                                        </div>
                                        <div className="form-group">
                                            <label>Output Path</label>
                                            <input
                                                type="text"
                                                value={currentNode.output_path || ''}
                                                onChange={(event) => updateNodeField(currentNode.id, 'output_path', event.target.value)}
                                            />
                                            {currentNodeOutputPathError ? (
                                                <div className="form-error">{currentNodeOutputPathError}</div>
                                            ) : (
                                                <small>Write tool output to a custom state path outside `input.*` and `messages`.</small>
                                            )}
                                        </div>
                                    </>
                                )}
                            </div>
                        )}

                        {selection.kind === 'edge' && currentEdge && (
                            <div className="graph-editor-inspector-body">
                                <div className="graph-editor-inspector-meta">
                                    <span className="graph-editor-chip edge">
                                        {currentEdge.source} {'->'} {currentEdge.target}
                                    </span>
                                    <button
                                        type="button"
                                        className="add-btn add-inline danger"
                                        onClick={deleteSelectedEdge}
                                    >
                                        Delete Edge
                                    </button>
                                </div>
                                <div className="form-group">
                                    <label>Edge ID</label>
                                    <input
                                        type="text"
                                        value={currentEdge.id || ''}
                                        onChange={(event) => updateEdgeField(currentEdge.id || '', 'id', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Label</label>
                                    <input
                                        type="text"
                                        value={currentEdge.label || ''}
                                        onChange={(event) => updateEdgeField(currentEdge.id || '', 'label', event.target.value)}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Priority</label>
                                    <input
                                        type="number"
                                        min={0}
                                        value={currentEdge.priority ?? 0}
                                        onChange={(event) =>
                                            updateEdgeField(
                                                currentEdge.id || '',
                                                'priority',
                                                Math.max(0, Number.parseInt(event.target.value || '0', 10) || 0)
                                            )
                                        }
                                    />
                                </div>
                                <div className="form-group">
                                    <label>Condition</label>
                                    <textarea
                                        rows={4}
                                        value={currentEdge.condition || ''}
                                        onChange={(event) => updateEdgeField(currentEdge.id || '', 'condition', event.target.value)}
                                        placeholder="state.foo == 'bar' and result.status == 'completed'"
                                    />
                                    {conditionError ? (
                                        <div className="form-error">{conditionError}</div>
                                    ) : (
                                        <small>Only `state` and `result` roots are allowed.</small>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </aside>
            </div>

            <div className={`graph-editor-advanced${showAdvancedJson ? ' open' : ''}`}>
                <div className="graph-editor-panel-title-row">
                    <h5>Advanced JSON</h5>
                    {jsonError && <span className="graph-editor-json-status error">Invalid JSON</span>}
                </div>
                {showAdvancedJson && (
                    <div className="graph-editor-advanced-body">
                        <textarea
                            rows={16}
                            value={jsonText}
                            onChange={(event) => onJsonTextChange(event.target.value)}
                            placeholder='[{"id":"default_linear_react","name":"Default Linear ReAct","initial_state":{},"nodes":[],"edges":[]}]'
                        />
                        {jsonError ? (
                            <div className="form-error">{jsonError}</div>
                        ) : (
                            <small>Full graphs JSON. Valid edits here rebuild the visual editor immediately.</small>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
