from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from agents.execution.providers.base import (
    ProviderAdapter,
    ProviderToolCall,
    ProviderToolResult,
    ProviderTurnEvent,
)
from tools import Tool


class TextReactAdapter(ProviderAdapter):
    name = "text_react"

    def supports(self, llm_client: Any) -> bool:
        return False

    def prepare_request_overrides(
        self,
        *,
        request_overrides: Dict[str, Any],
        tools: Iterable[Tool],
        llm_client: Any,
    ) -> Dict[str, Any]:
        return dict(request_overrides)

    async def run_turn(
        self,
        *,
        messages: List[Dict[str, Any]],
        llm_client: Any,
        request_overrides: Dict[str, Any],
    ):
        async for event in llm_client.chat_stream_events(messages, request_overrides or None):
            event_type = str(event.get("type", "") or "")
            if event_type in {"content", "reasoning"}:
                yield ProviderTurnEvent(
                    event_type=event_type,
                    delta=str(event.get("delta", "") or ""),
                )
                continue
            if event_type == "done":
                yield ProviderTurnEvent(event_type="done")

    def append_tool_results(
        self,
        *,
        messages: List[Dict[str, Any]],
        assistant_content: str,
        tool_calls: Sequence[ProviderToolCall],
        tool_results: Sequence[ProviderToolResult],
    ) -> None:
        messages.append({"role": "assistant", "content": assistant_content})
        for item in tool_results:
            messages.append(
                {
                    "role": "user",
                    "content": f"Tool {item.tool_name} returned:\n{item.content}",
                }
            )
