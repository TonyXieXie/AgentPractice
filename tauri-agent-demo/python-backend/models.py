from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal, List
from enum import Enum

# LLM API format and profile
LLMApiFormat = Literal["openai_chat_completions", "openai_responses"]
LLMProfile = Literal["openai", "openai_compatible", "deepseek", "zhipu"]
AgentMode = Literal["default", "super"]
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
    parent_session_id: Optional[str] = None


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    work_path: Optional[str] = None
    config_id: Optional[str] = None
    agent_profile: Optional[str] = None
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


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    blocked = "blocked"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TaskErrorCode(str, Enum):
    invalid_request = "invalid_request"
    task_not_found = "task_not_found"
    instance_not_found = "instance_not_found"
    route_not_resolved = "route_not_resolved"
    invalid_transition = "invalid_transition"
    loop_iteration_exceeded = "loop_iteration_exceeded"
    task_already_terminal = "task_already_terminal"
    cancelled_by_parent = "cancelled_by_parent"
    transient_retry_exhausted = "transient_retry_exhausted"
    internal_error = "internal_error"


class AgentInstance(BaseModel):
    id: str
    session_id: str
    profile_id: str
    name: Optional[str] = None
    abilities: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "disabled"] = "active"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AgentTask(BaseModel):
    id: str
    session_id: str
    title: Optional[str] = None
    input: str
    status: TaskStatus = TaskStatus.pending
    assigned_instance_id: Optional[str] = None
    created_by_instance_id: Optional[str] = None
    target_profile_id: Optional[str] = None
    required_abilities: List[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    loop_group_id: Optional[str] = None
    loop_iteration: int = 0
    max_retries: int = 2
    retry_count: int = 0
    idempotency_key: Optional[str] = None
    error_code: Optional[TaskErrorCode] = None
    error_message: Optional[str] = None
    result: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    legacy_child_session_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class AgentTaskCreateRequest(BaseModel):
    session_id: str
    title: Optional[str] = None
    input: str
    target_instance_id: Optional[str] = None
    target_profile_id: Optional[str] = None
    required_abilities: List[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    loop_group_id: Optional[str] = None
    loop_iteration: Optional[int] = None
    idempotency_key: Optional[str] = None
    max_retries: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentTaskHandoffRequest(BaseModel):
    title: Optional[str] = None
    input: Optional[str] = None
    target_instance_id: Optional[str] = None
    target_profile_id: Optional[str] = None
    required_abilities: List[str] = Field(default_factory=list)
    loop_group_id: Optional[str] = None
    loop_iteration: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentTaskCancelRequest(BaseModel):
    reason: Optional[str] = None
    propagate: bool = True


class AgentTaskEvent(BaseModel):
    id: Optional[int] = None
    task_id: str
    seq: int
    event_type: Literal[
        "task_started",
        "task_progress",
        "task_handoff",
        "task_completed",
        "task_failed",
        "task_cancelled"
    ]
    status: Optional[TaskStatus] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[TaskErrorCode] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class AgentArtifact(BaseModel):
    id: Optional[int] = None
    task_id: str
    session_id: str
    artifact_type: str
    path: Optional[str] = None
    uri: Optional[str] = None
    tree_hash: Optional[str] = None
    checksum: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
