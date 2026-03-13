from __future__ import annotations

from typing import Optional
from uuid import uuid4

from agents.execution.message_utils import (
    build_llm_request_overrides,
    get_execution_metadata,
    get_session_id,
    get_tool_arguments,
    get_tool_name,
    get_work_path,
    render_current_message,
    stream_enabled,
)
from agents.execution.prompt_assembler import PromptAssembler
from agents.execution.prompt_ir import PromptIR
from agents.execution.strategy import AgentStrategy, ExecutionContext, ExecutionStep
from agents.execution.tool_executor import ToolExecutor


class SimpleStrategy(AgentStrategy):
    name = "simple"

    def __init__(
        self,
        *,
        system_prompt: Optional[str] = None,
        max_history: int = 10,
    ) -> None:
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.max_history = max(0, max_history)

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
        tool_name = get_tool_name(message)
        if tool_name:
            async for step in self._execute_tool_request(
                message,
                agent_id=agent_id,
                tool_name=tool_name,
                tool_executor=resolved_tool_executor,
            ):
                yield step
            return

        if llm_client is None:
            yield ExecutionStep(
                step_type="answer",
                content=f"echo: {render_current_message(message)}",
                metadata={"mode": "echo_fallback"},
            )
            return

        if memory is None:
            raise RuntimeError("AgentMemory is required for llm execution")

        assembler = PromptAssembler()
        history_messages = await memory.build_history_for_agent(
            message,
            agent_id=agent_id,
            llm_client=llm_client,
            max_history_events=self.max_history,
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
        llm_overrides = build_llm_request_overrides(message)
        stream = stream_enabled(message)
        if not stream:
            response = await llm_client.chat(prompt_ir, llm_overrides or None)
            content = str(response.get("content", "") or "")
            yield ExecutionStep(
                step_type="answer",
                content=content,
                metadata={"raw_response": response.get("raw_response", {})},
            )
            return

        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        async for event in llm_client.chat_stream_events(prompt_ir, llm_overrides or None):
            event_type = str(event.get("type", "") or "")
            if event_type == "content":
                delta = str(event.get("delta", "") or "")
                if delta:
                    answer_parts.append(delta)
                    yield ExecutionStep("answer_delta", delta, {"stream_key": "answer"})
                continue
            if event_type == "reasoning":
                delta = str(event.get("delta", "") or "")
                if delta:
                    reasoning_parts.append(delta)
                    yield ExecutionStep(
                        "thought_delta",
                        delta,
                        {"stream_key": "reasoning", "reasoning": True},
                    )
                continue
            if event_type == "tool_call_delta":
                yield ExecutionStep(
                    "action_delta",
                    str(event.get("arguments_delta", "") or ""),
                    {
                        "tool_name": event.get("name"),
                        "tool_call_id": event.get("id"),
                        "status": "streaming",
                    },
                )
                continue

        if reasoning_parts:
            yield ExecutionStep(
                "thought",
                "".join(reasoning_parts),
                {"reasoning": True},
            )
        yield ExecutionStep("answer", "".join(answer_parts))

    async def _execute_tool_request(
        self,
        message,
        *,
        agent_id: str,
        tool_name: str,
        tool_executor: ToolExecutor,
    ):
        tool_call_id = uuid4().hex
        yield ExecutionStep(
            "action",
            f"tool:{tool_name}",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "running",
            },
        )
        result = await tool_executor.execute(
            agent_id=agent_id,
            run_id=message.run_id,
            message_id=message.id,
            session_id=get_session_id(message),
            work_path=get_work_path(message),
            metadata=get_execution_metadata(message),
            tool_name=tool_name,
            arguments=get_tool_arguments(message),
            tool_call_id=tool_call_id,
        )
        if result.directive is not None:
            yield ExecutionStep(
                "observation",
                tool_executor.serialize_output(result.output),
                {
                    "tool_call_id": result.tool_call_id,
                    "tool_name": tool_name,
                    "status": "completed",
                },
            )
            yield ExecutionStep(
                "directive",
                result.directive.kind,
                {
                    "tool_call_id": result.tool_call_id,
                    "tool_name": tool_name,
                    "directive": result.directive.to_dict(),
                },
            )
            return
        if result.ok:
            output_text = tool_executor.serialize_output(result.output)
            yield ExecutionStep(
                "observation",
                output_text,
                {
                    "tool_call_id": result.tool_call_id,
                    "tool_name": tool_name,
                    "status": "completed",
                },
            )
            yield ExecutionStep(
                "answer",
                output_text,
                {
                    "tool_call_id": result.tool_call_id,
                    "tool_name": tool_name,
                },
            )
            return

        error_text = str(result.error or f"Tool execution failed: {tool_name}")
        yield ExecutionStep(
            "observation",
            error_text,
            {
                "tool_call_id": result.tool_call_id,
                "tool_name": tool_name,
                "status": "error",
            },
        )
        yield ExecutionStep(
            "error",
            error_text,
            {
                "tool_call_id": result.tool_call_id,
                "tool_name": tool_name,
                "status": "error",
            },
        )
