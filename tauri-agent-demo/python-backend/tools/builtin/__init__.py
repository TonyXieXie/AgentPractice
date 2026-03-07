"""Built-in tool registration entrypoint."""

from ..base import ToolRegistry
from ..config import is_tool_enabled


def register_builtin_tools() -> None:
    """Register all built-in tools in the registry."""
    from .registry import iter_builtin_tools

    for tool in iter_builtin_tools():
        if is_tool_enabled(tool.name):
            ToolRegistry.register(tool)


__all__ = ["register_builtin_tools"]
