from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from agents.message import AgentMessage
from observation.events import ExecutionEvent
from observation.observer import ExecutionObserver, NullExecutionObserver


class AgentCenter:
    def __init__(self, observer: Optional[ExecutionObserver] = None) -> None:
        self.observer = observer or NullExecutionObserver()
        self._agents: Dict[str, "AgentBase"] = {}
        self._pending_rpcs: Dict[str, asyncio.Future[AgentMessage]] = {}
        self._lock = asyncio.Lock()
        self._seq = 0

    async def register(self, agent: "AgentBase") -> None:
        async with self._lock:
            self._agents[agent.agent_id] = agent

    async def unregister(self, agent_id: str) -> None:
        async with self._lock:
            self._agents.pop(agent_id, None)

    async def get(self, agent_id: str) -> Optional["AgentBase"]:
        async with self._lock:
            return self._agents.get(agent_id)

    async def list_agents(self) -> List["AgentBase"]:
        async with self._lock:
            return list(self._agents.values())

    async def route(self, message: AgentMessage):
        message = await self._assign_seq(message)
        await self._observe_message(message, "message.sent")

        if message.kind == "rpc_response":
            future = await self._pop_pending_rpc(message.correlation_id)
            if future is None:
                raise ValueError(f"No pending RPC for correlation_id={message.correlation_id}")
            if not future.done():
                future.set_result(message)
            return message

        if message.delivery == "broadcast":
            recipients = await self._resolve_broadcast_targets(message.sender_id)
            delivered = 0
            for agent in recipients:
                routed_message = message.model_copy(update={"target_id": agent.agent_id})
                await agent.receive(routed_message)
                delivered += 1
            return delivered

        target = await self.get(message.target_id or "")
        if target is None:
            raise ValueError(f"Target agent not found: {message.target_id}")

        if message.kind == "rpc_request":
            correlation_id = message.correlation_id or message.id
            loop = asyncio.get_running_loop()
            future: asyncio.Future[AgentMessage] = loop.create_future()
            await self._store_pending_rpc(correlation_id, future)

            async def _deliver_rpc() -> None:
                try:
                    maybe_response = await target.receive(message)
                    if isinstance(maybe_response, AgentMessage):
                        await self.route(maybe_response)
                except Exception as exc:
                    error_response = AgentMessage.build_rpc_response(
                        request=message,
                        sender_id=target.agent_id,
                        payload={"error": str(exc)},
                        ok=False,
                        visibility="internal",
                        level="error",
                    )
                    await self.route(error_response)

            asyncio.create_task(_deliver_rpc())
            timeout_sec = max(0.001, float(message.timeout_ms or 300_000) / 1000.0)
            try:
                return await asyncio.wait_for(future, timeout=timeout_sec)
            finally:
                await self._pop_pending_rpc(correlation_id)

        return await target.receive(message)

    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        return await self.observer.emit(event)

    async def _assign_seq(self, message: AgentMessage) -> AgentMessage:
        async with self._lock:
            self._seq += 1
            seq = self._seq
        return message.model_copy(update={"seq": seq})

    async def _observe_message(self, message: AgentMessage, event_type: str) -> ExecutionEvent:
        return await self.emit(
            ExecutionEvent(
                event_type=event_type,
                run_id=message.run_id,
                agent_id=message.sender_id,
                message_id=message.id,
                seq=message.seq,
                visibility=message.visibility,
                level=message.level,
                payload={
                    "topic": message.topic,
                    "kind": message.kind,
                    "delivery": message.delivery,
                    "target_id": message.target_id,
                    "correlation_id": message.correlation_id,
                },
            )
        )

    async def _resolve_broadcast_targets(self, sender_id: str) -> List["AgentBase"]:
        async with self._lock:
            return [
                agent
                for agent_id, agent in self._agents.items()
                if agent_id != sender_id
            ]

    async def _store_pending_rpc(
        self, correlation_id: str, future: asyncio.Future[AgentMessage]
    ) -> None:
        async with self._lock:
            self._pending_rpcs[correlation_id] = future

    async def _pop_pending_rpc(
        self, correlation_id: Optional[str]
    ) -> Optional[asyncio.Future[AgentMessage]]:
        if not correlation_id:
            return None
        async with self._lock:
            return self._pending_rpcs.pop(correlation_id, None)
