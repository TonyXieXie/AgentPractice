from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal, List

# LLM API format and profile
LLMApiFormat = Literal["openai_chat_completions", "openai_responses"]
LLMProfile = Literal["openai", "openai_compatible", "deepseek", "zhipu"]
AgentMode = Literal["default", "super"]
GraphNodeType = Literal["react_agent", "tool_call", "router"]


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
    max_context_tokens: int = Field(default=200000, ge=1000, le=1000000)
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
    max_context_tokens: int = 200000
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
    max_context_tokens: Optional[int] = None
    is_default: Optional[bool] = None
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh"]] = None
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = None


class MessageAttachment(BaseModel):
    id: Optional[int] = None
    message_id: int
    name: Optional[str] = None
    mime: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size: Optional[int] = None
    created_at: Optional[str] = None


class ChatMessage(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    raw_request: Optional[Dict[str, Any]] = None
    raw_response: Optional[Dict[str, Any]] = None
    attachments: Optional[List[MessageAttachment]] = None


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
    work_path: Optional[str] = None
    agent_profile: Optional[str] = None
    graph_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    context_summary: Optional[str] = None
    last_compressed_llm_call_id: Optional[int] = None
    context_estimate: Optional[Dict[str, Any]] = None
    context_estimate_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: Optional[int] = 0


class ChatSessionCreate(BaseModel):
    title: str = "New Chat"
    config_id: str
    work_path: Optional[str] = None
    agent_profile: Optional[str] = None
    graph_id: Optional[str] = None
    parent_session_id: Optional[str] = None


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    work_path: Optional[str] = None
    config_id: Optional[str] = None
    agent_profile: Optional[str] = None
    graph_id: Optional[str] = None
    parent_session_id: Optional[str] = None


class AttachmentInput(BaseModel):
    name: Optional[str] = None
    mime: Optional[str] = None
    data_base64: str
    width: Optional[int] = None
    height: Optional[int] = None
    size: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    config_id: Optional[str] = None
    work_path: Optional[str] = None
    agent_profile: Optional[str] = None
    graph_id: Optional[str] = None
    extra_work_paths: Optional[List[str]] = None
    agent_mode: Optional[AgentMode] = None
    shell_unrestricted: Optional[bool] = None
    attachments: Optional[List[AttachmentInput]] = None
    stream_id: Optional[str] = None
    last_seq: Optional[int] = None
    resume: Optional[bool] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    message_id: int


class GraphNode(BaseModel):
    id: str
    type: GraphNodeType
    name: Optional[str] = None
    description: Optional[str] = None
    profile_id: Optional[str] = None
    max_iterations: Optional[int] = Field(default=None, ge=1)
    input_template: Optional[Any] = None
    output_path: Optional[str] = None
    tool_name: Optional[str] = None
    args_template: Optional[Any] = None
    ui: Optional[Dict[str, Any]] = None


class GraphEdge(BaseModel):
    id: Optional[str] = None
    source: str
    target: str
    condition: Optional[str] = None
    priority: int = 0
    label: Optional[str] = None


class StateFieldDefinition(BaseModel):
    path: str
    type: Literal["string", "number", "boolean", "object", "array", "any"]
    mutable: bool = False


class StatePreset(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    state: Any = Field(default_factory=dict)
    state_schema: List[StateFieldDefinition] = Field(default_factory=list)


class GraphDefinition(BaseModel):
    id: str
    name: str
    initial_state: Any = Field(default_factory=dict)
    state_schema: List[StateFieldDefinition] = Field(default_factory=list)
    state_preset_id: Optional[str] = None
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    max_hops: int = Field(default=100, ge=1, le=10000)
    ui: Optional[Dict[str, Any]] = None


class EdgeExpressionContext(BaseModel):
    state: Any = Field(default_factory=dict)
    result: Any = None


class NodeResult(BaseModel):
    status: str
    output: Any = None
    state_patch: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None


class GraphRun(BaseModel):
    id: Optional[str] = None
    session_id: str
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None
    graph_id: str
    request_text: Optional[str] = None
    state_json: Any = Field(default_factory=dict)
    active_node_id: Optional[str] = None
    status: str
    hop_count: int = 0
    last_result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


class GraphNodeRun(BaseModel):
    id: Optional[str] = None
    graph_run_id: str
    node_id: str
    node_type: GraphNodeType
    sequence: int
    status: str
    input_json: Optional[Any] = None
    output_json: Optional[Any] = None
    state_patch_json: Optional[Any] = None
    error_json: Optional[Any] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None


class ExportRequest(BaseModel):
    session_id: Optional[str] = None
    format: Literal["json", "txt", "markdown"] = "json"


class ToolPermissionRequest(BaseModel):
    id: Optional[int] = None
    tool_name: str
    action: str
    path: str
    session_id: Optional[str] = None
    reason: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ToolPermissionRequestUpdate(BaseModel):
    status: str


class ChatStopRequest(BaseModel):
    message_id: Optional[int] = None
    session_id: Optional[str] = None


class RollbackRequest(BaseModel):
    message_id: int


class PatchRevertRequest(BaseModel):
    session_id: str
    revert_patch: str
    message_id: Optional[int] = None


class AstRequest(BaseModel):
    path: str
    mode: Optional[Literal["outline", "full"]] = None
    language: Optional[str] = None
    extensions: Optional[List[str]] = None
    max_files: Optional[int] = None
    max_symbols: Optional[int] = None
    max_nodes: Optional[int] = None
    max_depth: Optional[int] = None
    max_bytes: Optional[int] = None
    include_positions: Optional[bool] = None
    include_text: Optional[bool] = None
    session_id: Optional[str] = None
    work_path: Optional[str] = None
    extra_work_paths: Optional[List[str]] = None
    agent_mode: Optional[AgentMode] = None


class AstNotifyRequest(BaseModel):
    root: str
    paths: Optional[List[str]] = None


class AstSettingsRequest(BaseModel):
    root: str
    ignore_paths: Optional[List[str]] = None
    include_only_paths: Optional[List[str]] = None
    force_include_paths: Optional[List[str]] = None
    include_languages: Optional[List[str]] = None
    max_files: Optional[int] = None
