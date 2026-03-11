from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from agents.execution.tool_recorder import ToolEventRecorder
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
    def __init__(
        self,
        tools: Optional[Iterable[Tool]] = None,
        *,
        recorder: Optional[ToolEventRecorder] = None,
    ) -> None:
        self._tools: List[Tool] = list(tools or [])
        self._recorder = recorder

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
        agent_id: str,
        run_id: Optional[str],
        message_id: Optional[str],
        session_id: Optional[str],
        work_path: Optional[str],
        metadata: Optional[Dict[str, Any]],
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> ToolExecutionResult:
        resolved_call_id = tool_call_id or uuid4().hex
        tool = self.get_tool(tool_name)
        if tool is None:
            await self._record_tool_call(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            await self._record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                output=None,
                error=f"Tool not found: {tool_name}",
            )
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=f"Tool not found: {tool_name}",
            )

        payload = arguments if isinstance(arguments, dict) else {}
        if not tool.validate_input(payload):
            await self._record_tool_call(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                arguments=payload,
            )
            await self._record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                output=None,
                error=f"Invalid arguments for tool: {tool_name}",
            )
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=f"Invalid arguments for tool: {tool_name}",
            )

        await self._record_tool_call(
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            tool_call_id=resolved_call_id,
            tool_name=tool_name,
            arguments=payload,
        )
        token = set_tool_context(
            ToolContext(
                agent_id=agent_id,
                run_id=run_id,
                message_id=message_id,
                tool_call_id=resolved_call_id,
                work_path=work_path,
                metadata=dict(metadata or {}),
            )
        )
        try:
            output = await tool.execute(payload)
            await self._record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
                error=None,
            )
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=True,
                output=output,
            )
        except ToolExecutionError as exc:
            await self._record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                output=None,
                error=str(exc),
            )
            return ToolExecutionResult(
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            await self._record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=resolved_call_id,
                tool_name=tool_name,
                ok=False,
                output=None,
                error=str(exc),
            )
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

    async def _record_tool_call(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any] | Any,
    ) -> None:
        if self._recorder is None:
            return
        payload = arguments if isinstance(arguments, dict) else {"input": str(arguments)}
        try:
            await self._recorder.record_tool_call(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=payload,
            )
        except Exception:
            return

    async def _record_tool_result(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        tool_call_id: str,
        tool_name: str,
        ok: bool,
        output: Any,
        error: Optional[str],
    ) -> None:
        if self._recorder is None:
            return
        try:
            await self._recorder.record_tool_result(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                ok=ok,
                output=output,
                error=error,
            )
        except Exception:
            return
