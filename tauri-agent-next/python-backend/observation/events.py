from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agents.message import SeverityLevel, VisibilityLevel, utc_now_iso


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
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ExecutionSnapshot(BaseModel):
    run_id: Optional[str] = None
    status: str = "idle"
    latest_seq: int = 0
    agents: Dict[str, AgentSnapshot] = Field(default_factory=dict)
    tool_calls: Dict[str, ToolCallSnapshot] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)

    def apply(self, event: ExecutionEvent) -> "ExecutionSnapshot":
        update_payload: Dict[str, Any] = {
            "latest_seq": max(self.latest_seq, int(event.seq or 0)),
            "updated_at": event.created_at,
        }

        if event.run_id and not self.run_id:
            update_payload["run_id"] = event.run_id

        if event.event_type == "run.started":
            update_payload["status"] = "running"
        elif event.event_type == "run.finished":
            update_payload["status"] = "finished"

        snapshot = self.model_copy(update=update_payload, deep=True)

        if event.agent_id:
            agents = dict(snapshot.agents)
            current = agents.get(event.agent_id) or AgentSnapshot(
                agent_id=event.agent_id,
                status="idle",
            )
            agent_status = event.payload.get("status", current.status)
            agents[event.agent_id] = current.model_copy(
                update={
                    "status": agent_status,
                    "role": event.payload.get("role", current.role),
                    "updated_at": event.created_at,
                    "metadata": {
                        **current.metadata,
                        **(event.payload if event.event_type == "agent.state_changed" else {}),
                    },
                }
            )
            snapshot = snapshot.model_copy(update={"agents": agents}, deep=True)

        if event.tool_call_id:
            tool_calls = dict(snapshot.tool_calls)
            current_tool = tool_calls.get(event.tool_call_id) or ToolCallSnapshot(
                tool_call_id=event.tool_call_id,
                status="pending",
                agent_id=event.agent_id,
            )
            tool_calls[event.tool_call_id] = current_tool.model_copy(
                update={
                    "status": event.payload.get("status", current_tool.status),
                    "tool_name": event.payload.get("tool_name", current_tool.tool_name),
                    "agent_id": event.agent_id or current_tool.agent_id,
                    "updated_at": event.created_at,
                    "payload": {**current_tool.payload, **event.payload},
                }
            )
            snapshot = snapshot.model_copy(update={"tool_calls": tool_calls}, deep=True)

        return snapshot
