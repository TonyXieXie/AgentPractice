from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agents.execution.strategy import ExecutionStep

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage


class StepEmitter:
    def __init__(self, agent: "AgentBase") -> None:
        self.agent = agent

    async def emit_step(
        self,
        message: "AgentMessage",
        *,
        agent_id: str,
        step: ExecutionStep,
    ) -> None:
        payload = {
            "step_type": step.step_type,
            "content": step.content,
            **step.metadata,
        }
        if step.step_type == "error":
            payload.setdefault("error", step.content)
        tool_call_id = self._optional_str(step.metadata.get("tool_call_id"))
        task_id = self._extract_task_id(message)
        level = "error" if step.step_type == "error" else "info"
        await self.agent.observe(
            self._event_type_for_step(step),
            run_id=message.run_id,
            agent_id=agent_id,
            message_id=message.id,
            tool_call_id=tool_call_id,
            payload=payload,
            level=level,
            source_type=self._source_type_for_step(step),
            source_id=tool_call_id or agent_id,
            tags=self._tags_for_step(step),
            metadata={"task_id": task_id} if task_id else None,
        )

    def _event_type_for_step(self, step: ExecutionStep) -> str:
        if step.step_type == "action":
            return "tool.started"
        if step.step_type in {"observation", "observation_delta", "action_delta"}:
            return "tool.updated"
        if step.step_type == "directive":
            return "agent.directive_selected"
        if step.step_type in {"thought", "thought_delta", "answer_delta", "answer"}:
            return "llm.updated"
        if step.step_type == "error":
            return "agent.error"
        return "agent.step"

    def _stream_for_step(self, step: ExecutionStep) -> str:
        if step.step_type in {"thought", "thought_delta", "answer", "answer_delta"}:
            return "llm_chunk"
        if step.step_type in {"action", "action_delta", "observation", "observation_delta"}:
            return "tool_chunk"
        if step.step_type == "directive":
            return "directive"
        return "agent_event"

    def _source_type_for_step(self, step: ExecutionStep) -> str:
        if step.step_type in {"action", "action_delta", "observation", "observation_delta"}:
            return "tool"
        if step.step_type == "directive":
            return "engine"
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

    def _extract_task_id(self, message: "AgentMessage") -> str | None:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        return self._optional_str(metadata.get("task_id"))
