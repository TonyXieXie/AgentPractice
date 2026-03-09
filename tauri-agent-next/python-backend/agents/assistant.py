from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from agents.base import AgentBase
from agents.message import AgentMessage


RpcHandler = Callable[[AgentMessage], Awaitable[Dict[str, Any]] | Dict[str, Any] | Any]


class AssistantAgent(AgentBase):
    def __init__(
        self,
        *args,
        task_handler: Optional[RpcHandler] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_handlers: Dict[str, RpcHandler] = {}
        self.received_events: list[AgentMessage] = []
        if task_handler is not None:
            self.register_rpc_handler("task.run", task_handler)

    def register_rpc_handler(self, topic: str, handler: RpcHandler) -> None:
        self._rpc_handlers[topic] = handler

    async def on_message(self, message: AgentMessage):
        if message.kind == "event":
            self.received_events.append(message)
            return None

        if message.kind != "rpc_request":
            return None

        handler = self._rpc_handlers.get(message.topic) or self._default_handler
        await self.update_status("running", reason=message.topic)
        try:
            result = handler(message)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                result = {"result": result}
            await self.reply_rpc(message, result, ok=True)
        except Exception as exc:
            await self.reply_rpc(
                message,
                {"error": str(exc)},
                ok=False,
                level="error",
                visibility="internal",
            )
        finally:
            await self.update_status("idle", reason=message.topic)
        return None

    async def _default_handler(self, message: AgentMessage) -> Dict[str, Any]:
        content = str(message.payload.get("content", "") or "")
        return {
            "reply": content,
            "handled_by": self.agent_id,
            "topic": message.topic,
        }
