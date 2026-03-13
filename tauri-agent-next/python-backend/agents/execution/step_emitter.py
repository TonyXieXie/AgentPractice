from __future__ import annotations

from typing import TYPE_CHECKING

from agents.execution.message_utils import get_session_id
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
        if step.step_type not in {"thought", "answer", "error"}:
            return
        payload = {
            "step_type": step.step_type,
            "content": step.content,
            **step.metadata,
        }
        if step.step_type == "error":
            payload.setdefault("error", step.content)
        tool_call_id = self._optional_str(step.metadata.get("tool_call_id"))
        task_id = self._extract_task_id(message)
        metadata = {
            "session_id": get_session_id(message),
            "task_id": task_id,
        }
        trigger_fact_id = self._optional_str(
            (message.metadata or {}).get("trigger_fact_id")
            if isinstance(message.metadata, dict)
            else None
        )
        if trigger_fact_id:
            metadata["trigger_fact_id"] = trigger_fact_id
        await self.agent.observe(
            "llm.updated" if step.step_type in {"thought", "answer"} else "agent.error",
            run_id=message.run_id,
            agent_id=agent_id,
            message_id=message.id,
            tool_call_id=tool_call_id,
            payload=payload,
            level="error" if step.step_type == "error" else "info",
            source_type="llm" if step.step_type in {"thought", "answer"} else "engine",
            source_id=tool_call_id or agent_id,
            tags=[step.step_type],
            metadata=metadata,
        )

    def _optional_str(self, value) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _extract_task_id(self, message: "AgentMessage") -> str | None:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        return self._optional_str(metadata.get("task_id"))
