from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from observation.facts import PrivateExecutionEvent, SharedFact


LLMApiFormat = Literal["openai_chat_completions", "openai_responses"]
LLMProfile = Literal["openai", "openai_compatible", "deepseek", "zhipu"]


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
    reasoning_effort: Optional[
        Literal["none", "minimal", "low", "medium", "high", "xhigh"]
    ] = "medium"
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "detailed"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    config_path: str
    runtime_data_dir: str


class LogConfigRequest(BaseModel):
    backend_logic: Optional[bool] = None
    frontend_backend: Optional[bool] = None


class LogConfigResponse(BaseModel):
    ok: bool = True
    backend_logic: bool
    frontend_backend: bool


class CreateRunRequest(BaseModel):
    content: str
    strategy: Optional[str] = None
    session_id: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    llm_config: Optional[Dict[str, Any]] = None
    system_prompt: Optional[str] = None
    work_path: Optional[str] = None
    request_overrides: Dict[str, Any] = Field(default_factory=dict)


class CreateRunAcceptedResponse(BaseModel):
    ok: bool = True
    run_id: str
    session_id: str
    user_agent_id: str
    assistant_agent_id: str
    status: Literal["accepted"] = "accepted"


CreateRunResponse = CreateRunAcceptedResponse


class StopRunResponse(BaseModel):
    ok: bool = True
    run_id: str
    status: str


class SessionSharedFactsResponse(BaseModel):
    ok: bool = True
    session_id: str
    shared_facts: List[SharedFact] = Field(default_factory=list)
    next_after_seq: int = 0


class SessionPrivateFactsResponse(BaseModel):
    ok: bool = True
    session_id: str
    agent_id: str
    private_events: List[PrivateExecutionEvent] = Field(default_factory=list)
    next_after_id: int = 0


class PromptTraceSnapshot(BaseModel):
    id: int
    session_id: str
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    llm_model: Optional[str] = None
    max_context_tokens: int
    prompt_budget: int
    estimated_prompt_tokens: int
    rendered_message_count: int
    request_messages: List[Dict[str, Any]] = Field(default_factory=list)
    actions: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SessionPromptTraceResponse(BaseModel):
    ok: bool = True
    session_id: str
    agent_id: str
    prompt_trace: Optional[PromptTraceSnapshot] = None
