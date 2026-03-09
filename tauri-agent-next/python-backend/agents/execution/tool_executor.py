from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from agents.execution.strategy import ExecutionRequest
from tools import (
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolRegistry,
    reset_tool_context,
    set_tool_context,
)


@dataclass(slots=True)
class ToolExecutionResult:
    tool_call_id: str
    tool_name: str
    ok: bool
    output: Any = None
    error: Optional[str] = None


class ToolExecutor:
    def __init__(self, tools: Optional[Iterable[Tool]] = None) -> None:
        self._tools: List[Tool] = list(tools or [])

    def list_tools(self) -> List[Tool]:
        if self._tools:
            return list(self._tools)
        return ToolRegistry.get_all()

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        for tool in self.list_tools():
            if tool.name == tool_name:
                return tool
        return ToolRegistry.get(tool_name)

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        request: ExecutionRequest,
        tool_call_id: Optional[str] = None,
    ) -> ToolExecutionResult:
        resolved_call_id = tool_call_id or uuid4().hex
        tool = self.get_tool(tool_name)
        if tool is None:
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=f"Tool not found: {tool_name}",
            )

        payload = arguments if isinstance(arguments, dict) else {}
        if not tool.validate_input(payload):
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=f"Invalid arguments for tool: {tool_name}",
            )

        token = set_tool_context(
            ToolContext(
                agent_id=request.agent_id,
                run_id=request.run_id,
                message_id=request.message_id,
                tool_call_id=resolved_call_id,
                work_path=request.work_path,
                metadata=dict(request.metadata),
            )
        )
        try:
            output = await tool.execute(payload)
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
            )
        except ToolExecutionError as exc:
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=str(exc),
            )
        finally:
            reset_tool_context(token)

    def serialize_output(self, output: Any) -> str:
        if output is None:
            return ""
        if isinstance(output, str):
            return output
        return str(output)
