from __future__ import annotations

from typing import Any, Dict, Optional

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
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        payload = {"content": content}
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
