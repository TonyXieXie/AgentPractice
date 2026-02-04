export type LLMApiFormat = 'openai_chat_completions' | 'openai_responses';
export type LLMProfile = 'openai' | 'openai_compatible' | 'deepseek' | 'zhipu';
export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
export type ReasoningSummary = 'auto' | 'concise' | 'detailed';
export type AgentMode = 'default' | 'shell_safe' | 'super';

export interface AppConfig {
    llm?: {
        timeout_sec?: number;
    };
}

export interface AppConfigUpdate {
    llm?: {
        timeout_sec?: number;
    };
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
    created_at: string;
    updated_at: string;
    message_count?: number;
}

export interface ChatSessionCreate {
    title?: string;
    config_id: string;
    work_path?: string | null;
}

export interface ChatSessionUpdate {
    title?: string;
    work_path?: string | null;
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

export interface ChatRequest {
    message: string;
    session_id?: string;
    config_id?: string;
    work_path?: string | null;
    agent_mode?: AgentMode;
    shell_unrestricted?: boolean;
}

export interface ChatResponse {
    reply: string;
    session_id: string;
    message_id: number;
}
