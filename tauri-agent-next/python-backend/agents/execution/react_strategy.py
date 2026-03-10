from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agents.execution.providers import (
    OpenAIResponsesAdapter,
    OpenAIToolCallingAdapter,
    ProviderAdapter,
    ProviderToolResult,
    TextReactAdapter,
)
from agents.execution.simple_strategy import SimpleStrategy
from agents.execution.strategy import AgentStrategy, ExecutionRequest, ExecutionStep
from agents.execution.tool_executor import ToolExecutor


class ReactStrategy(AgentStrategy):
    name = "react"

    def __init__(
        self,
        *,
        system_prompt: Optional[str] = None,
        max_iterations: int = 4,
        providers: Optional[List[ProviderAdapter]] = None,
    ) -> None:
        self.system_prompt = system_prompt or (
            "You are a reasoning and acting assistant. "
            "Use tools when they are necessary, otherwise answer directly."
        )
        self.max_iterations = max(1, max_iterations)
        self._fallback = SimpleStrategy()
        self._providers = list(
            providers
            or [
                OpenAIResponsesAdapter(),
                OpenAIToolCallingAdapter(),
                TextReactAdapter(),
            ]
        )

    async def execute(
        self,
        request: ExecutionRequest,
        *,
        llm_client,
        tool_executor: ToolExecutor,
        context_builder,
    ):
        if llm_client is None:
            async for step in self._fallback.execute(
                request,
                llm_client=llm_client,
                tool_executor=tool_executor,
                context_builder=context_builder,
            ):
                yield step
            return

        tools = tool_executor.list_tools()
        messages = await context_builder.build_messages(
            request,
            llm_client=llm_client,
            default_system_prompt=self.system_prompt,
            max_history=10,
        )
        provider = self._resolve_provider(llm_client)
        llm_overrides = provider.prepare_request_overrides(
            request_overrides=context_builder.build_llm_request_overrides(request),
            tools=tools,
            llm_client=llm_client,
        )

        for iteration in range(self.max_iterations):
            assistant_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls = []

            async for event in provider.run_turn(
                messages=messages,
                llm_client=llm_client,
                request_overrides=llm_overrides,
            ):
                event_type = event.event_type
                if event_type == "content":
                    delta = event.delta
                    if delta:
                        assistant_parts.append(delta)
                        yield ExecutionStep(
                            "answer_delta",
                            delta,
                            {"stream_key": f"answer-{iteration}"},
                        )
                    continue

                if event_type == "reasoning":
                    delta = event.delta
                    if delta:
                        reasoning_parts.append(delta)
                        yield ExecutionStep(
                            "thought_delta",
                            delta,
                            {
                                "stream_key": f"reasoning-{iteration}",
                                "reasoning": True,
                                "iteration": iteration,
                            },
                        )
                    continue

                if event_type == "tool_call_delta":
                    call = event.tool_call
                    if call is None:
                        continue
                    yield ExecutionStep(
                        "action_delta",
                        event.delta,
                        {
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "status": "streaming",
                            "iteration": iteration,
                        },
                    )
                    continue

                if event_type == "done":
                    tool_calls = list(event.tool_calls)

            if reasoning_parts:
                yield ExecutionStep(
                    "thought",
                    "".join(reasoning_parts),
                    {"iteration": iteration, "reasoning": True},
                )

            if tool_calls:
                tool_results = []
                for call in self._ordered_tool_calls(tool_calls):
                    parsed_arguments, parse_error = self._parse_tool_arguments(call.arguments)
                    call_id = call.id
                    tool_name = call.name
                    yield ExecutionStep(
                        "action",
                        f"tool:{tool_name}",
                        {
                            "tool_call_id": call_id,
                            "tool_name": tool_name,
                            "status": "running",
                            "iteration": iteration,
                        },
                    )
                    if parse_error is not None:
                        yield ExecutionStep(
                            "observation",
                            parse_error,
                            {
                                "tool_call_id": call_id,
                                "tool_name": tool_name,
                                "status": "error",
                                "iteration": iteration,
                            },
                        )
                        tool_results.append(
                            ProviderToolResult(
                                tool_call_id=call_id,
                                tool_name=tool_name,
                                content=parse_error,
                                ok=False,
                            )
                        )
                        continue

                    result = await tool_executor.execute(
                        tool_name=tool_name,
                        arguments=parsed_arguments,
                        request=request,
                        tool_call_id=call_id,
                    )
                    content = (
                        tool_executor.serialize_output(result.output)
                        if result.ok
                        else str(result.error or f"Tool execution failed: {tool_name}")
                    )
                    yield ExecutionStep(
                        "observation",
                        content,
                        {
                            "tool_call_id": result.tool_call_id,
                            "tool_name": tool_name,
                            "status": "completed" if result.ok else "error",
                            "iteration": iteration,
                        },
                    )
                    tool_results.append(
                        ProviderToolResult(
                            tool_call_id=result.tool_call_id,
                            tool_name=tool_name,
                            content=content,
                            ok=result.ok,
                        )
                    )

                provider.append_tool_results(
                    messages=messages,
                    assistant_content="".join(assistant_parts),
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                )
                memory = getattr(context_builder, "memory", None)
                if memory is not None:
                    messages = await memory.ensure_budget_for_messages(
                        messages,
                        llm_client=llm_client,
                        session_id=getattr(request, "session_id", None),
                        agent_id=getattr(request, "agent_id", None),
                        run_id=getattr(request, "run_id", None),
                        phase="react_iteration",
                        iteration=iteration,
                    )
                continue

            answer = "".join(assistant_parts).strip()
            if answer:
                yield ExecutionStep(
                    "answer",
                    answer,
                    {"iteration": iteration, "strategy": "react"},
                )
                return

        yield ExecutionStep(
            "error",
            "react strategy reached max iterations without a final answer.",
            {"status": "error", "iterations": self.max_iterations},
        )

    def _parse_tool_arguments(self, raw_arguments: Any) -> tuple[Dict[str, Any], Optional[str]]:
        if raw_arguments in (None, ""):
            return {}, None
        if isinstance(raw_arguments, dict):
            return raw_arguments, None
        text = str(raw_arguments)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return {}, f"Invalid tool arguments JSON: {exc}"
        if not isinstance(payload, dict):
            return {}, "Tool arguments must decode to a JSON object."
        return payload, None

    def _resolve_provider(self, llm_client) -> ProviderAdapter:
        for provider in self._providers:
            if provider.supports(llm_client):
                return provider
        return self._providers[0]

    def _ordered_tool_calls(self, tool_calls):
        return sorted(tool_calls, key=lambda item: int(item.index))
