from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agents.message import SeverityLevel, VisibilityLevel, utc_now_iso


SEVERITY_ORDER: Dict[SeverityLevel, int] = {
    "debug": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
}


class ExecutionEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    seq: Optional[int] = None
    visibility: VisibilityLevel = "public"
    level: SeverityLevel = "info"
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class AgentSnapshot(BaseModel):
    agent_id: str
    status: str
    role: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCallSnapshot(BaseModel):
    tool_call_id: str
    status: str
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ExecutionSnapshot(BaseModel):
    run_id: Optional[str] = None
    status: str = "idle"
    latest_seq: int = 0
    latest_event_type: Optional[str] = None
    agents: Dict[str, AgentSnapshot] = Field(default_factory=dict)
    tool_calls: Dict[str, ToolCallSnapshot] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)

    def apply(self, event: ExecutionEvent) -> "ExecutionSnapshot":
        from observation.snapshot_builder import SnapshotBuilder

        return SnapshotBuilder().apply(self, event)


class RunProjection(BaseModel):
    run_id: str
    status: str = "idle"
    latest_seq: int = 0
    strategy: Optional[str] = None
    reply: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentProjection(BaseModel):
    run_id: str
    agent_id: str
    status: str
    role: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolCallProjection(BaseModel):
    run_id: str
    tool_call_id: str
    status: str
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    output: Optional[str] = None
    error: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class ExecutionProjectionState(BaseModel):
    run_projection: Optional[RunProjection] = None
    agent_projections: Dict[str, AgentProjection] = Field(default_factory=dict)
    tool_call_projections: Dict[str, ToolCallProjection] = Field(default_factory=dict)


def level_at_least(level: SeverityLevel, minimum: Optional[SeverityLevel]) -> bool:
    if minimum is None:
        return True
    return SEVERITY_ORDER.get(level, 0) >= SEVERITY_ORDER.get(minimum, 0)
