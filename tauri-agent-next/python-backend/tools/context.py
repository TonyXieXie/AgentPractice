from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(slots=True)
class ToolContext:
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    work_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "tool_call_id": self.tool_call_id,
            "work_path": self.work_path,
            "metadata": dict(self.metadata),
        }


_tool_context: ContextVar[ToolContext] = ContextVar(
    "_tool_context",
    default=ToolContext(),
)


def set_tool_context(value: ToolContext | Dict[str, Any] | None):
    if isinstance(value, ToolContext):
        context = value
    else:
        payload = value or {}
        context = ToolContext(
            agent_id=payload.get("agent_id"),
            run_id=payload.get("run_id"),
            message_id=payload.get("message_id"),
            tool_call_id=payload.get("tool_call_id"),
            work_path=payload.get("work_path"),
            metadata=dict(payload.get("metadata") or {}),
        )
    return _tool_context.set(context)


def reset_tool_context(token) -> None:
    try:
        _tool_context.reset(token)
    except ValueError:
        _tool_context.set(ToolContext())


def get_tool_context() -> ToolContext:
    return _tool_context.get()
