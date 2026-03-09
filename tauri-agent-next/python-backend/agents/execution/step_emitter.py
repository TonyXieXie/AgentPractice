from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agents.execution.strategy import ExecutionRequest, ExecutionStep

if TYPE_CHECKING:
    from agents.base import AgentBase


class StepEmitter:
    def __init__(self, agent: "AgentBase") -> None:
        self.agent = agent

    async def emit_run_started(
        self,
        request: ExecutionRequest,
        *,
        strategy_name: str,
    ) -> None:
        payload = {
            "strategy": strategy_name,
            "status": "running",
            "topic": "run.started",
        }
        await self.agent.observe(
            "run.started",
            run_id=request.run_id,
            message_id=request.message_id,
            payload=payload,
            source_type="engine",
            source_id=request.agent_id,
            tags=[strategy_name],
        )

    async def emit_run_finished(
        self,
        request: ExecutionRequest,
        *,
        strategy_name: str,
        status: str,
        reply: str,
    ) -> None:
        payload = {
            "strategy": strategy_name,
            "status": status,
            "reply": reply,
            "topic": "run.finished",
        }
        await self.agent.observe(
            "run.finished",
            run_id=request.run_id,
            message_id=request.message_id,
            payload=payload,
            level="info" if status == "completed" else "error",
            source_type="engine",
            source_id=request.agent_id,
            tags=[strategy_name, status],
        )

    async def emit_step(self, request: ExecutionRequest, step: ExecutionStep) -> None:
        payload = {
            "step_type": step.step_type,
            "content": step.content,
            **step.metadata,
        }
        tool_call_id = self._optional_str(step.metadata.get("tool_call_id"))
        level = "error" if step.step_type == "error" else "info"
        await self.agent.observe(
            self._event_type_for_step(step),
            run_id=request.run_id,
            agent_id=request.agent_id,
            message_id=request.message_id,
            tool_call_id=tool_call_id,
            payload=payload,
            level=level,
            source_type=self._source_type_for_step(step),
            source_id=tool_call_id or request.agent_id,
            tags=self._tags_for_step(step),
        )

    def _event_type_for_step(self, step: ExecutionStep) -> str:
        if step.step_type == "action":
            return "tool.started"
        if step.step_type in {"observation", "observation_delta", "action_delta"}:
            return "tool.updated"
        if step.step_type in {"thought", "thought_delta", "answer_delta", "answer"}:
            return "llm.updated"
        if step.step_type == "error":
            return "run.error"
        return "agent.step"

    def _stream_for_step(self, step: ExecutionStep) -> str:
        if step.step_type in {"thought", "thought_delta", "answer", "answer_delta"}:
            return "llm_chunk"
        if step.step_type in {"action", "action_delta", "observation", "observation_delta"}:
            return "tool_chunk"
        return "agent_event"

    def _source_type_for_step(self, step: ExecutionStep) -> str:
        if step.step_type in {"action", "action_delta", "observation", "observation_delta"}:
            return "tool"
        if step.step_type in {"thought", "thought_delta", "answer", "answer_delta"}:
            return "llm"
        return "engine"

    def _tags_for_step(self, step: ExecutionStep) -> list[str]:
        tags = [step.step_type, self._stream_for_step(step)]
        status = self._optional_str(step.metadata.get("status"))
        if status:
            tags.append(status)
        return tags

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
