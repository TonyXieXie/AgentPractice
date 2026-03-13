from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app_config import get_app_config
from agents.execution.message_utils import (
    build_llm_request_overrides,
    get_execution_metadata,
    get_session_id,
    get_tool_name,
    get_work_path,
)
from agents.execution.providers import (
    OpenAIResponsesAdapter,
    OpenAIToolCallingAdapter,
    ProviderAdapter,
    ProviderToolResult,
    TextReactAdapter,
)
from agents.execution.prompt_assembler import PromptAssembler
from agents.execution.prompt_ir import PromptIR
from agents.execution.simple_strategy import SimpleStrategy
from agents.execution.strategy import AgentStrategy, ExecutionContext, ExecutionStep
from agents.execution.tool_executor import ToolExecutor


_DEFAULT_REACT_MAX_ITERATIONS = 100


def _resolve_max_iterations(max_iterations: Optional[int]) -> int:
    candidate = max_iterations
    if candidate is None:
        agent_cfg = get_app_config().get("agent", {})
        if isinstance(agent_cfg, dict):
            candidate = agent_cfg.get("react_max_iterations")
    try:
        value = int(candidate if candidate is not None else _DEFAULT_REACT_MAX_ITERATIONS)
    except (TypeError, ValueError):
        value = _DEFAULT_REACT_MAX_ITERATIONS
    return max(1, value)


class ReactStrategy(AgentStrategy):
    name = "react"

    def __init__(
        self,
        *,
        system_prompt: Optional[str] = None,
        max_iterations: Optional[int] = None,
        providers: Optional[List[ProviderAdapter]] = None,
    ) -> None:
        self.system_prompt = system_prompt or (
            "You are a reasoning and acting assistant. "
            "Use tools when they are necessary, otherwise answer directly."
        )
        self.max_iterations = _resolve_max_iterations(max_iterations)
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
        message,
        *,
        agent_id: str,
        llm_client,
        tool_executor: ToolExecutor,
        memory,
        execution_context: Optional[ExecutionContext] = None,
    ):
        resolved_tool_executor = (
            execution_context.tool_executor
            if execution_context is not None and execution_context.tool_executor is not None
            else tool_executor
        )
        if get_tool_name(message):
            async for step in self._fallback.execute(
                message,
                agent_id=agent_id,
                llm_client=llm_client,
                tool_executor=resolved_tool_executor,
                memory=memory,
                execution_context=execution_context,
            ):
                yield step
            return
        if llm_client is None:
            async for step in self._fallback.execute(
                message,
                agent_id=agent_id,
                llm_client=llm_client,
                tool_executor=resolved_tool_executor,
                memory=memory,
                execution_context=execution_context,
            ):
                yield step
            return

        if memory is None:
            raise RuntimeError("AgentMemory is required for llm execution")

        tools = resolved_tool_executor.list_tools()
        assembler = PromptAssembler()
        history_messages = await memory.build_history_for_agent(
            message,
            agent_id=agent_id,
            llm_client=llm_client,
            max_history_events=10,
        )
        system_messages = assembler.build_system_messages(
            message,
            default_system_prompt=(
                execution_context.system_prompt
                if execution_context is not None and execution_context.system_prompt
                else self.system_prompt
            ),
            tool_policy_text=(
                execution_context.tool_policy_text
                if execution_context is not None
                else ""
            ),
            llm_client=llm_client,
        )
        current_input = assembler.build_current_input(message)
        prompt_ir = PromptIR(
            messages=assembler.assemble(
                system_messages=system_messages,
                history_messages=history_messages,
                current_input=current_input,
            ),
            budget={},
            trace={"cfg": memory._resolve_cfg(None), "actions": []},
        )
        prompt_ir = await memory.ensure_budget_for_view(
            prompt_ir,
            llm_client=llm_client,
            session_id=get_session_id(message),
            agent_id=agent_id,
            run_id=message.run_id,
            phase="build_view",
        )
        provider = self._resolve_provider(llm_client)
        llm_overrides = provider.prepare_request_overrides(
            request_overrides=build_llm_request_overrides(message),
            tools=tools,
            llm_client=llm_client,
        )

        for iteration in range(self.max_iterations):
            assistant_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls = []

            async for event in provider.run_turn(
                prompt_ir=prompt_ir,
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
                        await resolved_tool_executor.record_failed_tool_invocation(
                            agent_id=agent_id,
                            run_id=message.run_id,
                            message_id=message.id,
                            session_id=get_session_id(message),
                            metadata=get_execution_metadata(message),
                            tool_name=tool_name,
                            arguments=call.arguments,
                            error=parse_error,
                            tool_call_id=call_id,
                        )
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

                    result = await resolved_tool_executor.execute(
                        agent_id=agent_id,
                        run_id=message.run_id,
                        message_id=message.id,
                        session_id=get_session_id(message),
                        work_path=get_work_path(message),
                        metadata=get_execution_metadata(message),
                        tool_name=tool_name,
                        arguments=parsed_arguments,
                        tool_call_id=call_id,
                    )
                    if result.directive is not None:
                        yield ExecutionStep(
                            "observation",
                            resolved_tool_executor.serialize_output(result.output),
                            {
                                "tool_call_id": result.tool_call_id,
                                "tool_name": tool_name,
                                "status": "completed",
                                "iteration": iteration,
                            },
                        )
                        yield ExecutionStep(
                            "directive",
                            result.directive.kind,
                            {
                                "tool_call_id": result.tool_call_id,
                                "tool_name": tool_name,
                                "directive": result.directive.to_dict(),
                                "iteration": iteration,
                            },
                        )
                        return
                    content = (
                        resolved_tool_executor.serialize_output(result.output)
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
                    prompt_ir=prompt_ir,
                    assistant_content="".join(assistant_parts),
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                )
                prompt_ir = await memory.ensure_budget_for_view(
                    prompt_ir,
                    llm_client=llm_client,
                    session_id=get_session_id(message),
                    agent_id=agent_id,
                    run_id=message.run_id,
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
