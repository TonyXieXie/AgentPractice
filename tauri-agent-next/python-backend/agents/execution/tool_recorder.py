from __future__ import annotations

import json
from typing import Any, Dict, Optional, Protocol

from observation.center import ObservationCenter


class ToolEventRecorder(Protocol):
    async def record_tool_call(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        message_id: Optional[str],
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        ...

    async def record_tool_result(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        message_id: Optional[str],
        tool_call_id: str,
        tool_name: str,
        ok: bool,
        output: Any,
        error: Optional[str],
        metadata: Optional[Dict[str, Any]],
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
    omitted = max(
        0,
        len(raw)
        - len(head.encode("utf-8", errors="ignore"))
        - len(tail.encode("utf-8", errors="ignore")),
    )
    return f"{head}\n[TRUNCATED_STORAGE]({omitted} bytes omitted)\n{tail}"


class PrivateExecutionRecorder:
    def __init__(
        self,
        observation_center: ObservationCenter,
        *,
        max_payload_bytes: int = 1024 * 1024,
    ) -> None:
        self.observation_center = observation_center
        self.max_payload_bytes = max(16 * 1024, int(max_payload_bytes))

    async def record_tool_call(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        message_id: Optional[str],
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        if not session_id:
            return
        resolved_metadata = dict(metadata or {})
        payload: Dict[str, Any] = {
            "tool_name": tool_name,
            "arguments": (
                arguments if isinstance(arguments, dict) else {"input": str(arguments)}
            ),
        }
        await self.observation_center.append_private_event(
            session_id=session_id,
            owner_agent_id=agent_id,
            run_id=run_id,
            task_id=self._optional_text(resolved_metadata.get("task_id")),
            message_id=message_id,
            tool_call_id=tool_call_id,
            trigger_fact_id=self._optional_text(resolved_metadata.get("trigger_fact_id")),
            kind="tool_call",
            payload_json=payload,
        )

    async def record_tool_result(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        agent_id: str,
        message_id: Optional[str],
        tool_call_id: str,
        tool_name: str,
        ok: bool,
        output: Any,
        error: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        if not session_id:
            return
        resolved_metadata = dict(metadata or {})
        output_text = _truncate_storage_value(
            _serialize_output(output),
            max_bytes=self.max_payload_bytes,
        )
        error_text = (
            _truncate_storage_value(str(error or ""), max_bytes=self.max_payload_bytes)
            if error
            else ""
        )
        await self.observation_center.append_private_event(
            session_id=session_id,
            owner_agent_id=agent_id,
            run_id=run_id,
            task_id=self._optional_text(resolved_metadata.get("task_id")),
            message_id=message_id,
            tool_call_id=tool_call_id,
            trigger_fact_id=self._optional_text(resolved_metadata.get("trigger_fact_id")),
            kind="tool_result",
            payload_json={
                "tool_name": tool_name,
                "ok": ok,
                "output": output_text,
                "error": error_text,
            },
        )

    def _optional_text(self, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None
