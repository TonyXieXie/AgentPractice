from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.base import AgentBase
from agents.message import AgentMessage


class UserProxyAgent(AgentBase):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_events: list[AgentMessage] = []

    async def send_user_message(
        self,
        content: str,
        *,
        target_agent_id: str,
        topic: str = "task.run",
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        strategy: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        payload_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        payload: Dict[str, Any] = {"content": content}
        if strategy:
            payload["strategy"] = strategy
        if session_id is not None:
            payload["session_id"] = session_id
        if history is not None:
            payload["history"] = deepcopy(history)
        if llm_config is not None:
            payload["llm_config"] = deepcopy(llm_config)
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if work_path is not None:
            payload["work_path"] = work_path
        if request_overrides is not None:
            payload["request_overrides"] = deepcopy(request_overrides)
        if payload_overrides:
            payload.update(deepcopy(payload_overrides))
        return await self.call_rpc(
            topic,
            payload,
            target_agent_id=target_agent_id,
            run_id=run_id or self.run_id,
            metadata=metadata,
        )

    async def on_message(self, message: AgentMessage):
        if message.kind == "event":
            self.received_events.append(message)
            return None
        if message.kind == "rpc_request":
            await self.reply_rpc(
                message,
                {"error": f"Unsupported topic: {message.topic}"},
                ok=False,
                level="error",
                visibility="internal",
            )
        return None
