from contextvars import ContextVar
from typing import Any, Dict


_tool_context: ContextVar[Dict[str, Any]] = ContextVar("_tool_context", default={})


def set_tool_context(value: Dict[str, Any]):
    return _tool_context.set(value or {})


def reset_tool_context(token) -> None:
    _tool_context.reset(token)


def get_tool_context() -> Dict[str, Any]:
    return _tool_context.get() or {}
