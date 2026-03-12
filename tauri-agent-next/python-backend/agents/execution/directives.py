from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


MESSAGE_DIRECTIVE_KINDS = frozenset(
    {
        "send_rpc_request",
        "send_rpc_response",
        "send_event",
        "broadcast_event",
    }
)

TERMINAL_DIRECTIVE_KINDS = frozenset(
    {
        "finish_run",
        "fail_run",
        "stop_run",
    }
)

DIRECTIVE_KINDS = MESSAGE_DIRECTIVE_KINDS | TERMINAL_DIRECTIVE_KINDS

RESERVED_DIRECTIVE_TOOL_NAMES = frozenset(DIRECTIVE_KINDS)


def allowed_directive_kinds_for_agent(
    *,
    agent_type: Optional[str],
    role: Optional[str],
) -> Set[str]:
    normalized_agent_type = str(agent_type or "").strip().lower()
    normalized_role = str(role or "").strip().lower()
    if normalized_agent_type == "user_proxy" or normalized_role == "user_proxy":
        return set(DIRECTIVE_KINDS)
    return set(MESSAGE_DIRECTIVE_KINDS)
@dataclass(slots=True)
class ExecutionDirective:
    kind: str
    args: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_kind = str(self.kind or "").strip()
        if normalized_kind not in DIRECTIVE_KINDS:
            raise ValueError(f"Unsupported execution directive: {self.kind}")
        self.kind = normalized_kind
        self.args = dict(self.args or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "args": dict(self.args),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExecutionDirective":
        if not isinstance(payload, dict):
            raise ValueError("directive payload must be a dict")
        kind = str(payload.get("kind") or "").strip()
        args = payload.get("args")
        if not isinstance(args, dict):
            args = {}
        return cls(kind=kind, args=args)


def directive_from_output(value: Any) -> Optional[ExecutionDirective]:
    if isinstance(value, ExecutionDirective):
        return value
    if not isinstance(value, dict):
        return None
    if str(value.get("__directive__") or "").strip() != "execution":
        return None
    try:
        return ExecutionDirective(
            kind=str(value.get("kind") or "").strip(),
            args=dict(value.get("args") or {}),
        )
    except Exception:
        return None
