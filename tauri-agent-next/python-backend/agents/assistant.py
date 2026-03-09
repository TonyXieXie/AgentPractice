from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from agents.base import AgentBase
from agents.execution import ExecutionEngine
from agents.message import AgentMessage


RpcHandler = Callable[[AgentMessage], Awaitable[Dict[str, Any]] | Dict[str, Any] | Any]


class AssistantAgent(AgentBase):
    def __init__(
        self,
        *args,
        task_handler: Optional[RpcHandler] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_handlers: Dict[str, RpcHandler] = {}
        self.received_events: list[AgentMessage] = []
        self.execution_engine = execution_engine or ExecutionEngine(self)
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

        await self.update_status("running", reason=message.topic)
        try:
            if message.topic in self._rpc_handlers:
                result_payload, ok = await self._execute_handler(
                    self._rpc_handlers[message.topic],
                    message,
                )
                await self.reply_rpc(
                    message,
                    result_payload,
                    ok=ok,
                    level="info" if ok else "error",
                    visibility="public" if ok else "internal",
                )
                return None

            if message.topic == "task.run":
                result = await self.execution_engine.execute(message)
                await self.reply_rpc(
                    message,
                    result.payload,
                    ok=result.ok,
                    level="info" if result.ok else "error",
                    visibility="public" if result.ok else "internal",
                )
                return None

            await self.reply_rpc(
                message,
                await self._default_handler(message),
                ok=True,
            )
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

    async def _execute_handler(
        self,
        handler: RpcHandler,
        message: AgentMessage,
    ) -> tuple[Dict[str, Any], bool]:
        result = handler(message)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, tuple) and len(result) == 2:
            payload, ok = result
            if not isinstance(payload, dict):
                payload = {"result": payload}
            return payload, bool(ok)
        if not isinstance(result, dict):
            result = {"result": result}
        return result, True

    async def _default_handler(self, message: AgentMessage) -> Dict[str, Any]:
        content = str(message.payload.get("content", "") or "")
        return {
            "reply": content,
            "handled_by": self.agent_id,
            "topic": message.topic,
        }
