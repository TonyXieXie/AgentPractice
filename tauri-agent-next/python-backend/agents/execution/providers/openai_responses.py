from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from agents.execution.prompt_ir import PromptIR
from agents.execution.providers.base import (
    ProviderAdapter,
    ProviderToolCall,
    ProviderToolResult,
    ProviderTurnEvent,
)
from tools import Tool, tool_to_openai_responses_tool


class OpenAIResponsesAdapter(ProviderAdapter):
    name = "openai_responses"

    def supports(self, llm_client: Any) -> bool:
        api_format = str(getattr(getattr(llm_client, "config", None), "api_format", "") or "")
        return api_format.lower() == "openai_responses"

    def prepare_request_overrides(
        self,
        *,
        request_overrides: Dict[str, Any],
        tools: Iterable[Tool],
        llm_client: Any,
    ) -> Dict[str, Any]:
        payload = dict(request_overrides)
        tool_payload = [tool_to_openai_responses_tool(tool) for tool in tools]
        if tool_payload:
            payload["tools"] = tool_payload
        return payload

    async def run_turn(
        self,
        *,
        prompt_ir: PromptIR,
        llm_client: Any,
        request_overrides: Dict[str, Any],
    ):
        tool_calls: Dict[str, ProviderToolCall] = {}
        async for event in llm_client.chat_stream_events(prompt_ir, request_overrides or None):
            event_type = str(event.get("type", "") or "")
            if event_type == "content":
                yield ProviderTurnEvent(
                    event_type="content",
                    delta=str(event.get("delta", "") or ""),
                )
                continue
            if event_type == "reasoning":
                yield ProviderTurnEvent(
                    event_type="reasoning",
                    delta=str(event.get("delta", "") or ""),
                )
                continue
            if event_type == "tool_call_delta":
                call = self._merge_tool_call(tool_calls, event)
                yield ProviderTurnEvent(
                    event_type="tool_call_delta",
                    delta=str(event.get("arguments_delta", "") or ""),
                    tool_call=call,
                )
                continue
            if event_type == "done":
                for item in event.get("tool_calls", []) or []:
                    self._merge_tool_call(tool_calls, item)
                yield ProviderTurnEvent(
                    event_type="done",
                    tool_calls=self._ordered_tool_calls(tool_calls.values()),
                )

    def append_tool_results(
        self,
        *,
        prompt_ir: PromptIR,
        assistant_content: str,
        tool_calls: Sequence[ProviderToolCall],
        tool_results: Sequence[ProviderToolResult],
    ) -> None:
        for call in self._ordered_tool_calls(tool_calls):
            prompt_ir.messages.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
        for item in tool_results:
            prompt_ir.messages.append(
                {
                    "type": "function_call_output",
                    "call_id": item.tool_call_id,
                    "output": item.content,
                }
            )

    def _merge_tool_call(
        self,
        tool_calls: Dict[str, ProviderToolCall],
        event: Dict[str, Any],
    ) -> ProviderToolCall:
        key = str(event.get("id") or event.get("call_id") or event.get("index") or len(tool_calls))
        current = tool_calls.get(key)
        call = ProviderToolCall(
            id=str(event.get("id") or event.get("call_id") or getattr(current, "id", key)),
            name=str(event.get("name") or getattr(current, "name", "") or ""),
            arguments=str(event.get("arguments") or getattr(current, "arguments", "") or ""),
            index=int(event.get("index", getattr(current, "index", len(tool_calls))) or 0),
        )
        delta = str(event.get("arguments_delta", "") or "")
        if delta:
            call.arguments = f"{call.arguments}{delta}"
        tool_calls[key] = call
        return call

    def _ordered_tool_calls(
        self,
        tool_calls: Iterable[ProviderToolCall],
    ) -> List[ProviderToolCall]:
        return sorted(tool_calls, key=lambda item: int(item.index))
