from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from agents.instance import AgentInstance, AgentStatus
from agents.message import AgentMessage, SeverityLevel, VisibilityLevel
from observation.center import ObservationCenter


class AgentBase(ABC):
    def __init__(
        self,
        instance: AgentInstance,
        center: "AgentCenter",
        observer: Optional[ObservationCenter] = None,
    ) -> None:
        self.instance = instance
        self.center = center
        self.observer = observer or getattr(center, "observation_center", None)

    @property
    def agent_id(self) -> str:
        return self.instance.id

    @property
    def run_id(self) -> Optional[str]:
        return self.instance.run_id

    async def receive(self, message: AgentMessage):
        return await self.on_message(message)

    async def send_event(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        target_agent_id: Optional[str] = None,
        target_profile: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        target_id, resolved_target_profile, target_metadata = self._normalize_target(
            target_agent_id=target_agent_id,
            target_profile=target_profile,
        )
        resolved_metadata = dict(metadata or {})
        resolved_metadata.update(target_metadata)
        message = AgentMessage.build_event(
            topic=topic,
            sender_id=self.agent_id,
            target_id=target_id,
            target_profile=resolved_target_profile,
            payload=payload,
            run_id=run_id or self.run_id,
            session_id=session_id,
            delivery="unicast",
            visibility=visibility,
            level=level,
            metadata=resolved_metadata,
        )
        await self.center.route(message)

    async def broadcast_event(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        message = AgentMessage.build_event(
            topic=topic,
            sender_id=self.agent_id,
            payload=payload,
            run_id=run_id or self.run_id,
            session_id=session_id,
            delivery="broadcast",
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        return await self.center.route(message)

    async def send_rpc_request(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        target_agent_id: Optional[str] = None,
        target_profile: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout_ms: int = 300_000,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        message = self._build_outbound_rpc_request(
            topic=topic,
            payload=payload,
            target_agent_id=target_agent_id,
            target_profile=target_profile,
            run_id=run_id,
            session_id=session_id,
            timeout_ms=timeout_ms,
            visibility=visibility,
            level=level,
            metadata=metadata,
        )
        await self.center.route(message)
        return message

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
        await self.center.route(response)
        return response

    def _build_outbound_rpc_request(
        self,
        *,
        topic: str,
        payload: Dict[str, Any],
        target_agent_id: Optional[str] = None,
        target_profile: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout_ms: int = 300_000,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        target_id, resolved_target_profile, target_metadata = self._normalize_target(
            target_agent_id=target_agent_id,
            target_profile=target_profile,
        )
        resolved_metadata = dict(metadata or {})
        resolved_metadata.update(target_metadata)
        return AgentMessage.build_rpc_request(
            topic=topic,
            sender_id=self.agent_id,
            target_id=target_id,
            target_profile=resolved_target_profile,
            payload=payload,
            run_id=run_id or self.run_id,
            session_id=session_id,
            timeout_ms=timeout_ms,
            visibility=visibility,
            level=level,
            metadata=resolved_metadata,
        )

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
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        observation_center = self.observer
        resolved_metadata = dict(metadata or {})
        session_id = self._resolve_session_id(payload, resolved_metadata)
        if observation_center is None or not session_id:
            return None
        private_kind = self._private_kind_for_event_type(
            event_type,
            payload=payload or {},
            level=level,
        )
        if private_kind is None:
            return None
        return await observation_center.append_private_event(
            session_id=session_id,
            owner_agent_id=agent_id or self.agent_id,
            run_id=run_id or self.run_id,
            task_id=self._optional_text(resolved_metadata.get("task_id")),
            message_id=message_id,
            tool_call_id=tool_call_id,
            trigger_fact_id=self._optional_text(resolved_metadata.get("trigger_fact_id")),
            kind=private_kind,
            payload_json=self._private_payload_for_event(
                event_type=event_type,
                payload=payload or {},
                metadata=resolved_metadata,
                source_type=source_type,
                source_id=source_id,
                tags=tags,
                level=level,
                visibility=visibility,
            ),
        )

    async def update_status(
        self,
        status: AgentStatus,
        *,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
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
            source_type="agent",
            metadata=merged_metadata,
        )

    @abstractmethod
    async def on_message(self, message: AgentMessage):
        ...

    def _normalize_target(
        self,
        *,
        target_agent_id: Optional[str],
        target_profile: Optional[str],
    ) -> tuple[Optional[str], Optional[str], Dict[str, Any]]:
        normalized_target_id = str(target_agent_id or "").strip() or None
        normalized_target_profile = str(target_profile or "").strip() or None
        if normalized_target_id is None and normalized_target_profile is None:
            raise ValueError("target_agent_id or target_profile is required")
        metadata: Dict[str, Any] = {}
        if normalized_target_profile is not None:
            metadata["target_profile"] = normalized_target_profile
        if normalized_target_id is not None:
            normalized_target_profile = None
        return normalized_target_id, normalized_target_profile, metadata

    def _private_kind_for_event_type(
        self,
        event_type: str,
        *,
        payload: Dict[str, Any],
        level: SeverityLevel,
    ) -> str | None:
        if event_type in {"assistant.protocol_error", "user_proxy.protocol_error", "agent.error"}:
            return "execution_error"
        if event_type == "agent.state_changed":
            return "reasoning_note"
        if event_type == "llm.updated":
            step_type = str(payload.get("step_type") or "").strip()
            if step_type == "thought":
                return "reasoning_note"
            if step_type == "answer":
                return "reasoning_summary"
            return None
        if level == "error":
            return "execution_error"
        return None

    def _private_payload_for_event(
        self,
        *,
        event_type: str,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        source_type: Optional[str],
        source_id: Optional[str],
        tags: Optional[list[str]],
        level: SeverityLevel,
        visibility: VisibilityLevel,
    ) -> Dict[str, Any]:
        result = dict(payload)
        result.update(
            {
                "event_type": event_type,
                "source_type": source_type,
                "source_id": source_id,
                "tags": list(tags or []),
                "level": level,
                "visibility": visibility,
            }
        )
        if metadata:
            result["metadata"] = metadata
        return result

    def _resolve_session_id(
        self,
        payload: Optional[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> str | None:
        if metadata.get("session_id"):
            return self._optional_text(metadata.get("session_id"))
        if isinstance(payload, dict) and payload.get("session_id"):
            return self._optional_text(payload.get("session_id"))
        return None

    def _optional_text(self, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.center import AgentCenter
