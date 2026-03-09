from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from agents.instance import AgentInstance, AgentStatus
from agents.message import AgentMessage, SeverityLevel, VisibilityLevel
from observation.events import ExecutionEvent
from observation.observer import ExecutionObserver, NullExecutionObserver


class AgentBase(ABC):
    def __init__(
        self,
        instance: AgentInstance,
        center: "AgentCenter",
        observer: Optional[ExecutionObserver] = None,
    ) -> None:
        self.instance = instance
        self.center = center
        self.observer = observer or getattr(center, "observer", None) or NullExecutionObserver()

    @property
    def agent_id(self) -> str:
        return self.instance.id

    @property
    def run_id(self) -> Optional[str]:
        return self.instance.run_id

    async def receive(self, message: AgentMessage):
        await self.observe(
            "message.received",
            payload={
                "topic": message.topic,
                "kind": message.kind,
                "sender_id": message.sender_id,
                "delivery": message.delivery,
            },
            run_id=message.run_id or self.run_id,
            message_id=message.id,
            level=message.level,
            visibility=message.visibility,
        )
        return await self.on_message(message)

    async def send_event(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        target_agent_id: str,
        run_id: Optional[str] = None,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        message = AgentMessage.build_event(
            topic=topic,
            sender_id=self.agent_id,
            target_id=target_agent_id,
            payload=payload,
            run_id=run_id or self.run_id,
            delivery="unicast",
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        await self.center.route(message)

    async def broadcast_event(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        run_id: Optional[str] = None,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        message = AgentMessage.build_event(
            topic=topic,
            sender_id=self.agent_id,
            payload=payload,
            run_id=run_id or self.run_id,
            delivery="broadcast",
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        return await self.center.route(message)

    async def call_rpc(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        target_agent_id: str,
        run_id: Optional[str] = None,
        timeout_ms: int = 300_000,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        message = AgentMessage.build_rpc_request(
            topic=topic,
            sender_id=self.agent_id,
            target_id=target_agent_id,
            payload=payload,
            run_id=run_id or self.run_id,
            timeout_ms=timeout_ms,
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        response = await self.center.route(message)
        if not isinstance(response, AgentMessage):
            raise RuntimeError("RPC route did not return AgentMessage response")
        return response

    async def reply_rpc(
        self,
        request: AgentMessage,
        payload: Dict[str, Any],
        *,
        ok: bool = True,
        visibility: Optional[VisibilityLevel] = None,
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        response = AgentMessage.build_rpc_response(
            request=request,
            sender_id=self.agent_id,
            payload=payload,
            ok=ok,
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        routed = await self.center.route(response)
        if not isinstance(routed, AgentMessage):
            raise RuntimeError("RPC response route did not return AgentMessage")
        return routed

    async def observe(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            event_type=event_type,
            run_id=run_id or self.run_id,
            agent_id=agent_id or self.agent_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            visibility=visibility,
            level=level,
            payload=payload or {},
            metadata=metadata or {},
        )
        return await self.observer.emit(event)

    async def update_status(
        self,
        status: AgentStatus,
        *,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionEvent:
        merged_metadata = dict(metadata or {})
        if reason:
            merged_metadata["reason"] = reason
        self.instance = self.instance.with_status(status, **merged_metadata)
        return await self.observe(
            "agent.state_changed",
            payload={
                "status": status,
                "role": self.instance.role,
                **merged_metadata,
            },
            level="info" if status != "error" else "error",
        )

    @abstractmethod
    async def on_message(self, message: AgentMessage):
        ...
