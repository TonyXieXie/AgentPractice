// LLM API 类型
export type LLMApiType = 'openai' | 'zhipu' | 'deepseek';

// LLM 配置
export interface LLMConfig {
    id: string;
    name: string;
    api_type: LLMApiType;
    api_key: string;
    base_url?: string;
    model: string;
    temperature: number;
    max_tokens: number;
    is_default: boolean;
    created_at: string;
}

export interface LLMConfigCreate {
    name: string;
    api_type: LLMApiType;
    api_key: string;
    base_url?: string;
    model: string;
    temperature?: number;
    max_tokens?: number;
    is_default?: boolean;
}

export interface LLMConfigUpdate {
    name?: string;
    api_key?: string;
    base_url?: string;
    model?: string;
    temperature?: number;
    max_tokens?: number;
    is_default?: boolean;
}

// 聊天消息
export interface Message {
    id: number;
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp: string;
    metadata?: Record<string, any>;
    raw_request?: Record<string, any>;  // LLM原始请求数据
    raw_response?: Record<string, any>; // LLM原始响应数据
}

export interface MessageCreate {
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    metadata?: Record<string, any>;
}

// 会话
export interface ChatSession {
    id: string;
    title: string;
    config_id: string;
    created_at: string;
    updated_at: string;
    message_count?: number;
}

export interface ChatSessionCreate {
    title?: string;
    config_id: string;
}

export interface ChatSessionUpdate {
    title?: string;
}

// API 请求/响应
export interface ChatRequest {
    message: string;
    session_id?: string;
    config_id?: string;
}

export interface ChatResponse {
    reply: string;
    session_id: string;
    message_id: number;
}

export interface ExportRequest {
    session_id?: string;
    format?: 'json' | 'txt' | 'markdown';
}
