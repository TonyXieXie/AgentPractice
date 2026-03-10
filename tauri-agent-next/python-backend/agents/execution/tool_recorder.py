from __future__ import annotations

import json
from typing import Any, Dict, Optional, Protocol

from agents.execution.strategy import ExecutionRequest
from repositories.conversation_repository import ConversationRepository


class ToolEventRecorder(Protocol):
    async def record_tool_call(
        self,
        *,
        request: ExecutionRequest,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> None:
        ...

    async def record_tool_result(
        self,
        *,
        request: ExecutionRequest,
        tool_call_id: str,
        tool_name: str,
        ok: bool,
        output: Any,
        error: Optional[str],
    ) -> None:
        ...


def _serialize_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, (dict, list)):
        try:
            return json.dumps(output, ensure_ascii=False, indent=2)
        except TypeError:
            return str(output)
    return str(output)


def _truncate_storage_value(text: str, *, max_bytes: int) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= max_bytes:
        return text
    head_bytes = max_bytes // 2
    tail_bytes = max_bytes - head_bytes
    head = raw[:head_bytes].decode("utf-8", errors="ignore")
    tail = raw[-tail_bytes:].decode("utf-8", errors="ignore")
    omitted = max(0, len(raw) - len(head.encode("utf-8", errors="ignore")) - len(tail.encode("utf-8", errors="ignore")))
    return f"{head}\n[TRUNCATED_STORAGE]({omitted} bytes omitted)\n{tail}"


class ConversationToolRecorder:
    def __init__(
        self,
        conversation_repository: ConversationRepository,
        *,
        max_payload_bytes: int = 1024 * 1024,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.max_payload_bytes = max(16 * 1024, int(max_payload_bytes))

    async def record_tool_call(
        self,
        *,
        request: ExecutionRequest,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> None:
        session_id = request.session_id
        if not session_id:
            return
        payload: Dict[str, Any] = {"arguments": arguments if isinstance(arguments, dict) else {"input": str(arguments)}}
        await self.conversation_repository.append_event(
            session_id=session_id,
            run_id=request.run_id,
            kind="tool_call",
            content=payload,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )

    async def record_tool_result(
        self,
        *,
        request: ExecutionRequest,
        tool_call_id: str,
        tool_name: str,
        ok: bool,
        output: Any,
        error: Optional[str],
    ) -> None:
        session_id = request.session_id
        if not session_id:
            return
        output_text = _truncate_storage_value(_serialize_output(output), max_bytes=self.max_payload_bytes)
        error_text = _truncate_storage_value(str(error or ""), max_bytes=self.max_payload_bytes) if error else ""
        await self.conversation_repository.append_event(
            session_id=session_id,
            run_id=request.run_id,
            kind="tool_result",
            content={"output": output_text, "error": error_text},
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            ok=ok,
        )
