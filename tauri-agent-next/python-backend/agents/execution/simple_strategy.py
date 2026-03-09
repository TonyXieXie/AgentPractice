from __future__ import annotations

from typing import Optional
from uuid import uuid4

from agents.execution.strategy import AgentStrategy, ExecutionRequest, ExecutionStep
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
        request: ExecutionRequest,
        *,
        llm_client,
        tool_executor: ToolExecutor,
        context_builder,
    ):
        if request.tool_name:
            async for step in self._execute_tool_request(request, tool_executor):
                yield step
            return

        if llm_client is None:
            yield ExecutionStep(
                step_type="answer",
                content=f"echo: {request.user_input}",
                metadata={"mode": "echo_fallback"},
            )
            return

        messages = context_builder.build_messages(
            request,
            llm_client=llm_client,
            default_system_prompt=self.system_prompt,
            max_history=self.max_history,
        )
        llm_overrides = context_builder.build_llm_request_overrides(request)
        stream = bool(request.request_overrides.get("stream", True))
        if not stream:
            response = await llm_client.chat(messages, llm_overrides or None)
            content = str(response.get("content", "") or "")
            yield ExecutionStep(
                step_type="answer",
                content=content,
                metadata={"raw_response": response.get("raw_response", {})},
            )
            return

        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        async for event in llm_client.chat_stream_events(messages, llm_overrides or None):
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
        request: ExecutionRequest,
        tool_executor: ToolExecutor,
    ):
        tool_name = request.tool_name or ""
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
            tool_name=tool_name,
            arguments=request.tool_arguments,
            request=request,
            tool_call_id=tool_call_id,
        )
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
