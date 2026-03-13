from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from agents.execution.prompt_ir import PromptIR
from agents.execution.providers.base import (
    ProviderAdapter,
    ProviderToolCall,
    ProviderToolResult,
    ProviderTurnEvent,
)
from tools import Tool, tool_to_openai_function


def _merge_streamed_arguments(
    existing_arguments: Any,
    *,
    arguments: Any,
    arguments_delta: Any,
) -> str:
    current_text = str(existing_arguments or "")
    delta_text = str(arguments_delta or "")
    if arguments is None:
        if not delta_text:
            return current_text
        return f"{current_text}{delta_text}"

    explicit_text = str(arguments or "")
    if not explicit_text:
        if not delta_text:
            return ""
        return f"{current_text}{delta_text}"
    if not delta_text:
        return explicit_text
    if explicit_text == delta_text:
        if current_text and not explicit_text.startswith(current_text):
            return f"{current_text}{delta_text}"
        return explicit_text
    return explicit_text


class OpenAIToolCallingAdapter(ProviderAdapter):
    name = "openai_tool_calling"

    def supports(self, llm_client: Any) -> bool:
        api_format = str(getattr(getattr(llm_client, "config", None), "api_format", "") or "")
        return api_format.lower() != "openai_responses"

    def prepare_request_overrides(
        self,
        *,
        request_overrides: Dict[str, Any],
        tools: Iterable[Tool],
        llm_client: Any,
    ) -> Dict[str, Any]:
        payload = dict(request_overrides)
        tool_payload = [tool_to_openai_function(tool) for tool in tools]
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
                call = self._merge_tool_call_delta(tool_calls, event)
                yield ProviderTurnEvent(
                    event_type="tool_call_delta",
                    delta=str(event.get("arguments_delta", "") or ""),
                    tool_call=call,
                )
                continue
            if event_type == "done":
                for item in event.get("tool_calls", []) or []:
                    self._merge_done_tool_call(tool_calls, item)
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
        prompt_ir.messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in self._ordered_tool_calls(tool_calls)
                ],
            }
        )
        for item in tool_results:
            prompt_ir.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_call_id,
                    "name": item.tool_name,
                    "content": item.content,
                }
            )

    def _merge_tool_call_delta(
        self,
        tool_calls: Dict[str, ProviderToolCall],
        event: Dict[str, Any],
    ) -> ProviderToolCall:
        key = str(event.get("id") or event.get("index") or len(tool_calls))
        current = tool_calls.get(key)
        merged_arguments = _merge_streamed_arguments(
            getattr(current, "arguments", ""),
            arguments=event.get("arguments"),
            arguments_delta=event.get("arguments_delta"),
        )
        call = ProviderToolCall(
            id=str(event.get("id") or getattr(current, "id", key)),
            name=str(event.get("name") or getattr(current, "name", "") or ""),
            arguments=merged_arguments,
            index=int(event.get("index", getattr(current, "index", len(tool_calls))) or 0),
        )
        tool_calls[key] = call
        return call

    def _merge_done_tool_call(
        self,
        tool_calls: Dict[str, ProviderToolCall],
        item: Dict[str, Any],
    ) -> None:
        key = str(item.get("id") or item.get("index") or len(tool_calls))
        current = tool_calls.get(key)
        tool_calls[key] = ProviderToolCall(
            id=str(item.get("id") or getattr(current, "id", key)),
            name=str(item.get("name") or getattr(current, "name", "") or ""),
            arguments=_merge_streamed_arguments(
                getattr(current, "arguments", ""),
                arguments=item.get("arguments"),
                arguments_delta=item.get("arguments_delta"),
            ),
            index=int(item.get("index", getattr(current, "index", len(tool_calls))) or 0),
        )

    def _ordered_tool_calls(
        self,
        tool_calls: Iterable[ProviderToolCall],
    ) -> List[ProviderToolCall]:
        return sorted(tool_calls, key=lambda item: int(item.index))
