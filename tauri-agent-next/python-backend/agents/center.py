from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Sequence

from agents.message import AgentMessage
from observation.events import ExecutionEvent
from observation.observer import ExecutionObserver, NullExecutionObserver
from repositories.message_center_repository import MessageCenterRepository


class AgentCenter:
    def __init__(
        self,
        observer: Optional[ExecutionObserver] = None,
        message_center_repository: Optional[MessageCenterRepository] = None,
    ) -> None:
        self.observer = observer or NullExecutionObserver()
        self.message_center_repository = message_center_repository
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
        await self._persist_message(message)

        if message.kind == "rpc_response":
            future = await self._pop_pending_rpc(message.correlation_id)
            if future is None:
                raise ValueError(f"No pending RPC for correlation_id={message.correlation_id}")
            if not future.done():
                future.set_result(message)
            await self._maybe_observe_run_finished(message)
            return message

        if message.delivery == "broadcast":
            recipients = await self._resolve_broadcast_targets(message.sender_id)
            await self._persist_broadcast_targets(message, recipients)
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

    async def _maybe_observe_run_finished(self, message: AgentMessage) -> None:
        if message.topic != "task.run":
            return
        if not message.run_id:
            return
        payload = message.payload or {}
        status = str(payload.get("status") or ("completed" if message.ok else "error"))
        reply = str(payload.get("reply") or payload.get("error") or "")
        strategy = str(payload.get("strategy") or "")
        await self.emit(
            ExecutionEvent(
                event_type="run.finished",
                run_id=message.run_id,
                agent_id=message.sender_id,
                message_id=message.correlation_id,
                visibility=message.visibility,
                level="info" if status == "completed" else "error",
                source_type="engine",
                source_id=message.sender_id,
                tags=[tag for tag in [strategy, status] if tag],
                payload={
                    "strategy": strategy,
                    "status": status,
                    "reply": reply,
                    "topic": "run.finished",
                },
            )
        )

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
                visibility=message.visibility,
                level=message.level,
                source_type="center",
                source_id=message.sender_id,
                tags=[message.kind, message.delivery],
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

    async def _persist_message(self, message: AgentMessage) -> None:
        repo = self.message_center_repository
        if repo is None:
            return
        session_id = _extract_session_id(message)
        if not session_id:
            return
        viewers = _resolve_unicast_viewers(message)
        if not viewers:
            return
        for viewer in viewers:
            try:
                await repo.append_visible_message(
                    session_id=session_id,
                    viewer_agent_id=viewer,
                    message=message,
                )
            except Exception:
                continue

    async def _persist_broadcast_targets(
        self,
        message: AgentMessage,
        recipients: Sequence["AgentBase"],
    ) -> None:
        repo = self.message_center_repository
        if repo is None:
            return
        session_id = _extract_session_id(message)
        if not session_id:
            return
        viewers = [message.sender_id, *[agent.agent_id for agent in recipients]]
        for viewer in viewers:
            try:
                await repo.append_visible_message(
                    session_id=session_id,
                    viewer_agent_id=viewer,
                    message=message,
                )
            except Exception:
                continue

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


def _extract_session_id(message: AgentMessage) -> Optional[str]:
    if message.session_id:
        return str(message.session_id)
    payload = message.payload or {}
    if isinstance(payload, dict) and payload.get("session_id"):
        return str(payload.get("session_id"))
    metadata = message.metadata or {}
    if isinstance(metadata, dict) and metadata.get("session_id"):
        return str(metadata.get("session_id"))
    return None


def _resolve_unicast_viewers(message: AgentMessage) -> list[str]:
    viewers: list[str] = []
    if message.sender_id:
        viewers.append(str(message.sender_id))
    if message.target_id:
        viewers.append(str(message.target_id))
    # For broadcast, viewers are handled separately (sender + recipients).
    if message.delivery == "broadcast":
        return []
    deduped: list[str] = []
    seen = set()
    for item in viewers:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
