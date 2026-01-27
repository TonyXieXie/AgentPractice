from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from datetime import datetime

# LLM API 类型
LLMApiType = Literal["openai", "zhipu", "deepseek"]

# LLM 配置模型
class LLMConfig(BaseModel):
    id: Optional[str] = None
    name: str
    api_type: LLMApiType
    api_key: str
    base_url: Optional[str] = None
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=32000)
    is_default: bool = False
    created_at: Optional[str] = None

class LLMConfigCreate(BaseModel):
    name: str
    api_type: LLMApiType
    api_key: str
    base_url: Optional[str] = None
    model: str
    temperature: float = 0.7
    max_tokens: int = 2000
    is_default: bool = False

class LLMConfigUpdate(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    is_default: Optional[bool] = None

# 聊天消息模型
class ChatMessage(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    raw_request: Optional[Dict[str, Any]] = None  # LLM原始请求
    raw_response: Optional[Dict[str, Any]] = None  # LLM原始响应

class ChatMessageCreate(BaseModel):
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    metadata: Optional[Dict[str, Any]] = None
    raw_request: Optional[Dict[str, Any]] = None  # LLM原始请求
    raw_response: Optional[Dict[str, Any]] = None  # LLM原始响应

# 会话模型
class ChatSession(BaseModel):
    id: Optional[str] = None
    title: str
    config_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: Optional[int] = 0

class ChatSessionCreate(BaseModel):
    title: str = "新对话"
    config_id: str

class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None

# API 请求/响应模型
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    config_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    session_id: str
    message_id: int

class ExportRequest(BaseModel):
    session_id: Optional[str] = None  # None 表示导出所有会话
    format: Literal["json", "txt", "markdown"] = "json"
