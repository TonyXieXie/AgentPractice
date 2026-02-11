export type LLMApiFormat = 'openai_chat_completions' | 'openai_responses';
export type LLMProfile = 'openai' | 'openai_compatible' | 'deepseek' | 'zhipu';
export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
export type ReasoningSummary = 'auto' | 'concise' | 'detailed';
export type AgentMode = 'default' | 'shell_safe' | 'super';

export interface ToolDefinition {
    name: string;
    description?: string;
    parameters?: Record<string, any>[];
}

export interface AgentAbility {
    id: string;
    name: string;
    type: string;
    prompt?: string;
    tools?: string[];
    params?: Record<string, any>;
}

export interface AgentProfile {
    id: string;
    name: string;
    abilities: string[];
    params?: Record<string, any>;
}

export interface CodeMapConfig {
    enabled?: boolean;
    max_symbols?: number;
    max_files?: number;
    max_lines?: number;
    weight_refs?: number;
    weight_mentions?: number;
}

export interface AgentConfig {
    base_system_prompt?: string;
    react_max_iterations?: number;
    ast_enabled?: boolean;
    code_map?: CodeMapConfig;
    abilities?: AgentAbility[];
    profiles?: AgentProfile[];
    default_profile?: string;
}

export interface ContextConfig {
    compression_enabled?: boolean;
    compress_start_pct?: number;
    compress_target_pct?: number;
    min_keep_messages?: number;
    keep_recent_calls?: number;
    step_calls?: number;
    truncate_long_data?: boolean;
    long_data_threshold?: number;
    long_data_head_chars?: number;
    long_data_tail_chars?: number;
}

export interface ContextEstimate {
    total: number;
    system: number;
    history: number;
    tools: number;
    other: number;
    max_tokens?: number;
    updated_at?: string;
}

export interface AppConfig {
    llm?: {
        timeout_sec?: number;
    };
    context?: ContextConfig;
    agent?: AgentConfig;
}

export interface AppConfigUpdate {
    llm?: {
        timeout_sec?: number;
    };
    context?: ContextConfig;
    agent?: AgentConfig;
}

export interface LLMConfig {
    id: string;
    name: string;
    api_format: LLMApiFormat;
    api_profile: LLMProfile;
    api_key: string;
    base_url?: string;
    model: string;
    temperature: number;
    max_tokens: number;
    max_context_tokens: number;
    is_default: boolean;
    created_at: string;
    reasoning_effort?: ReasoningEffort;
    reasoning_summary?: ReasoningSummary;
}

export interface LLMConfigCreate {
    name: string;
    api_format: LLMApiFormat;
    api_profile: LLMProfile;
    api_key: string;
    base_url?: string;
    model: string;
    temperature?: number;
    max_tokens?: number;
    max_context_tokens?: number;
    is_default?: boolean;
    reasoning_effort?: ReasoningEffort;
    reasoning_summary?: ReasoningSummary;
}

export interface LLMConfigUpdate {
    name?: string;
    api_key?: string;
    base_url?: string;
    model?: string;
    api_format?: LLMApiFormat;
    api_profile?: LLMProfile;
    temperature?: number;
    max_tokens?: number;
    max_context_tokens?: number;
    is_default?: boolean;
    reasoning_effort?: ReasoningEffort;
    reasoning_summary?: ReasoningSummary;
}

export interface Message {
    id: number;
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp: string;
    metadata?: Record<string, any>;
    raw_request?: Record<string, any>;
    raw_response?: Record<string, any>;
    attachments?: MessageAttachment[];
}

export interface MessageCreate {
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    metadata?: Record<string, any>;
}

export interface ChatSession {
    id: string;
    title: string;
    config_id: string;
    work_path?: string | null;
    agent_profile?: string | null;
    context_summary?: string | null;
    last_compressed_llm_call_id?: number | null;
    context_estimate?: ContextEstimate | null;
    context_estimate_at?: string | null;
    created_at: string;
    updated_at: string;
    message_count?: number;
}

export interface ChatSessionCreate {
    title?: string;
    config_id: string;
    work_path?: string | null;
    agent_profile?: string | null;
}

export interface ChatSessionUpdate {
    title?: string;
    work_path?: string | null;
    config_id?: string;
    agent_profile?: string | null;
}

export interface LLMCall {
    id: number;
    session_id: string;
    message_id?: number | null;
    agent_type?: string | null;
    iteration?: number | null;
    stream: boolean;
    api_profile?: string | null;
    api_format?: string | null;
    model?: string | null;
    request_json?: Record<string, any> | null;
    response_json?: Record<string, any> | null;
    response_text?: string | null;
    processed_json?: Record<string, any> | null;
    created_at: string;
}

export interface ToolPermissionRequest {
    id: number;
    tool_name: string;
    action: string;
    path: string;
    session_id?: string | null;
    reason?: string | null;
    status: string;
    created_at?: string | null;
    updated_at?: string | null;
}

export interface ApplyPatchSummary {
    path: string;
    added: number;
    removed: number;
}

export interface ApplyPatchResult {
    ok: boolean;
    summary?: ApplyPatchSummary[];
    diff?: string;
    revert_patch?: string;
    error?: string;
}

export interface PatchRevertResponse {
    ok: boolean;
    result?: ApplyPatchResult;
    user_message_id?: number;
    assistant_message_id?: number;
}

export interface AstNode {
    type?: string;
    name?: string;
    attr?: string;
    value?: string;
    text?: string;
    start?: [number, number];
    end?: [number, number];
    children?: AstNode[];
}

export interface AstSymbol {
    kind?: string;
    name?: string;
    parent?: string;
    signature?: string;
    bases?: string[];
    start?: [number, number];
    end?: [number, number];
}

export interface AstPayload {
    ok?: boolean;
    path?: string;
    mode?: string;
    language?: string;
    files?: AstPayload[];
    symbols?: AstSymbol[];
    imports?: any[];
    ast?: AstNode;
    truncated?: boolean;
    error?: string;
}

export interface AstRequest {
    path: string;
    mode?: 'outline' | 'full';
    language?: string;
    extensions?: string[];
    max_files?: number;
    max_symbols?: number;
    max_nodes?: number;
    max_depth?: number;
    max_bytes?: number;
    include_positions?: boolean;
    include_text?: boolean;
    session_id?: string;
    work_path?: string | null;
    extra_work_paths?: string[] | null;
    agent_mode?: AgentMode;
}

export interface AstPathSettings {
    root?: string;
    ignore_paths?: string[];
    include_only_paths?: string[];
    force_include_paths?: string[];
    include_languages?: string[];
    max_files?: number;
}

export interface AstSettingsResponse {
    ok?: boolean;
    root?: string;
    settings?: AstPathSettings;
}

export interface AstCacheFile {
    path: string;
    file_mtime?: number;
    parsed_at?: number;
    stale?: boolean;
    include_text?: boolean;
    payload?: AstPayload;
}

export interface AstCacheResponse {
    ok?: boolean;
    root?: string;
    files?: AstCacheFile[];
}

export interface CodeMapResponse {
    ok?: boolean;
    prompt?: string;
}

export interface ChatRequest {
    message: string;
    session_id?: string;
    config_id?: string;
    work_path?: string | null;
    agent_profile?: string | null;
    extra_work_paths?: string[] | null;
    agent_mode?: AgentMode;
    shell_unrestricted?: boolean;
    attachments?: ChatAttachmentInput[];
}

export interface MessageAttachment {
    id?: number;
    message_id?: number;
    name?: string;
    mime?: string;
    width?: number;
    height?: number;
    size?: number;
    created_at?: string;
    preview_url?: string;
    local_id?: string;
}

export interface ChatAttachmentInput {
    name?: string;
    mime?: string;
    data_base64: string;
    width?: number;
    height?: number;
    size?: number;
}

export interface ChatResponse {
    reply: string;
    session_id: string;
    message_id: number;
}
