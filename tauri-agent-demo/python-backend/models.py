from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal

# LLM API format and profile
LLMApiFormat = Literal["openai_chat_completions", "openai_responses"]
LLMProfile = Literal["openai", "openai_compatible", "deepseek", "zhipu"]
AgentMode = Literal["default", "shell_safe", "super"]
class LLMConfig(BaseModel):
    id: Optional[str] = None
    name: str
    api_format: LLMApiFormat = "openai_chat_completions"
    api_profile: LLMProfile = "openai"
    api_key: str
    base_url: Optional[str] = None
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=32000)
    is_default: bool = False
    created_at: Optional[str] = None
    # Reasoning params (OpenAI o1/GPT-5 models)
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh"]] = "medium"
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "detailed"


class LLMConfigCreate(BaseModel):
    name: str
    api_format: Optional[LLMApiFormat] = None
    api_profile: Optional[LLMProfile] = None
    # Deprecated: accept api_type for backward compatibility
    api_type: Optional[LLMProfile] = None
    api_key: str
    base_url: Optional[str] = None
    model: str
    temperature: float = 0.7
    max_tokens: int = 2000
    is_default: bool = False
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh"]] = "medium"
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "detailed"


class LLMConfigUpdate(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_format: Optional[LLMApiFormat] = None
    api_profile: Optional[LLMProfile] = None
    # Deprecated: accept api_type for backward compatibility
    api_type: Optional[LLMProfile] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    is_default: Optional[bool] = None
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh"]] = None
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = None


class ChatMessage(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    raw_request: Optional[Dict[str, Any]] = None
    raw_response: Optional[Dict[str, Any]] = None


class ChatMessageCreate(BaseModel):
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    metadata: Optional[Dict[str, Any]] = None
    raw_request: Optional[Dict[str, Any]] = None
    raw_response: Optional[Dict[str, Any]] = None


class ChatSession(BaseModel):
    id: Optional[str] = None
    title: str
    config_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: Optional[int] = 0


class ChatSessionCreate(BaseModel):
    title: str = "New Chat"
    config_id: str


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    config_id: Optional[str] = None
    agent_mode: Optional[AgentMode] = None
    shell_unrestricted: Optional[bool] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    message_id: int


class ExportRequest(BaseModel):
    session_id: Optional[str] = None
    format: Literal["json", "txt", "markdown"] = "json"


class ToolPermissionRequest(BaseModel):
    id: Optional[int] = None
    tool_name: str
    action: str
    path: str
    reason: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ToolPermissionRequestUpdate(BaseModel):
    status: str


class ChatStopRequest(BaseModel):
    message_id: int


class RollbackRequest(BaseModel):
    message_id: int
