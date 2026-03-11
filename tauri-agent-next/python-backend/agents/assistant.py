from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from agents.base import AgentBase
from agents.execution import ExecutionEngine, TaskManager
from agents.message import AgentMessage
from repositories.agent_profile_repository import AgentProfileRepository


RpcHandler = Callable[[AgentMessage], Awaitable[Dict[str, Any]] | Dict[str, Any] | Any]


class AssistantAgent(AgentBase):
    def __init__(
        self,
        *args,
        task_handler: Optional[RpcHandler] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        task_manager: Optional[TaskManager] = None,
        profile_repository: Optional[AgentProfileRepository] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_handlers: Dict[str, RpcHandler] = {}
        self.received_events: list[AgentMessage] = []
        self.execution_engine = execution_engine or ExecutionEngine(self)
        self.task_manager = task_manager
        self.profile_repository = profile_repository
        if task_handler is not None:
            self.register_rpc_handler("task.run", task_handler)

    def register_rpc_handler(self, topic: str, handler: RpcHandler) -> None:
        self._rpc_handlers[topic] = handler

    async def on_message(self, message: AgentMessage):
        if message.message_type == "event":
            self.received_events.append(message)
            if self.task_manager is not None and await self._should_host_event(message):
                await self.task_manager.host_message_task(
                    message,
                    agent=self,
                    execution_engine=self.execution_engine,
                )
            return None

        if message.message_type == "rpc" and message.rpc_phase == "response":
            if self.task_manager is None:
                raise RuntimeError("TaskManager is required for assistant rpc_response handling")
            await self.task_manager.host_message_task(
                message,
                agent=self,
                execution_engine=self.execution_engine,
            )
            return None

        if message.message_type != "rpc" or message.rpc_phase != "request":
            return None

        if message.topic in self._rpc_handlers:
            await self.update_status("running", reason=message.topic)
            try:
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

        try:
            if self.task_manager is None:
                raise RuntimeError("TaskManager is required for assistant rpc_request handling")
            result = await self.task_manager.host_message_task(
                message,
                agent=self,
                execution_engine=self.execution_engine,
            )
            await self.reply_rpc(
                message,
                result.payload,
                ok=result.ok,
                level="info" if result.ok else "error",
                visibility="public" if result.ok else "internal",
            )
        except Exception as exc:
            await self.reply_rpc(
                message,
                {"error": str(exc)},
                ok=False,
                level="error",
                visibility="internal",
            )
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

    async def _should_host_event(self, message: AgentMessage) -> bool:
        repository = self.profile_repository
        if repository is None:
            return False
        profile_id = str(self.instance.profile_id or repository.default_profile_id).strip()
        if not profile_id:
            return False
        profile = await repository.get(profile_id)
        if profile is None:
            return False
        return profile.can_execute_event(message.topic)

