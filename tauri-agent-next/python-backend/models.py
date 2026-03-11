from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from observation.events import (
    AgentProjection,
    ExecutionEvent,
    ExecutionSnapshot,
    RunProjection,
    ToolCallProjection,
)


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


class CreateRunQueuedResponse(BaseModel):
    ok: bool = True
    ticket_id: str
    session_id: str
    status: Literal["queued"] = "queued"


CreateRunResponse = CreateRunAcceptedResponse | CreateRunQueuedResponse


class StopRunResponse(BaseModel):
    ok: bool = True
    run_id: str
    status: str


class RunTicketResponse(BaseModel):
    ok: bool = True
    ticket_id: str
    session_id: str
    status: Literal["queued", "started", "rejected"]
    run_id: Optional[str] = None
    error: Optional[str] = None


class RunSnapshotResponse(BaseModel):
    ok: bool = True
    run_id: str
    snapshot: ExecutionSnapshot
    run_projection: Optional[RunProjection] = None
    agent_projections: Dict[str, AgentProjection] = Field(default_factory=dict)
    tool_call_projections: Dict[str, ToolCallProjection] = Field(default_factory=dict)


class ListRunEventsResponse(BaseModel):
    ok: bool = True
    run_id: str
    events: List[ExecutionEvent] = Field(default_factory=list)
    next_after_seq: int = 0
