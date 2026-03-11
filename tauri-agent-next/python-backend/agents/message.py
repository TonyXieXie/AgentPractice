from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


AgentMessageType = Literal["rpc", "event"]
AgentObjectType = Literal["target", "broadcast"]
RpcPhase = Literal["request", "response"]
AgentDelivery = Literal["unicast", "broadcast"]
VisibilityLevel = Literal["public", "internal", "debug"]
SeverityLevel = Literal["debug", "info", "warning", "error"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentMessage(BaseModel):
    class TargetRef(BaseModel):
        agent_id: Optional[str] = None
        profile_id: Optional[str] = None

        @model_validator(mode="after")
        def validate_shape(self) -> "AgentMessage.TargetRef":
            agent_id = str(self.agent_id or "").strip()
            profile_id = str(self.profile_id or "").strip()
            if agent_id and profile_id:
                raise ValueError("target.agent_id and target.profile_id are mutually exclusive")
            if not agent_id and not profile_id:
                raise ValueError("target requires agent_id or profile_id")
            return self

    id: str = Field(default_factory=lambda: uuid4().hex)
    message_type: AgentMessageType
    object_type: AgentObjectType = "target"
    rpc_phase: Optional[RpcPhase] = None
    topic: str
    sender_id: str
    target: Optional[TargetRef] = None
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    seq: Optional[int] = None
    visibility: VisibilityLevel = "public"
    level: SeverityLevel = "info"
    ok: Optional[bool] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: Optional[int] = None
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def validate_shape(self) -> "AgentMessage":
        normalized_topic = str(self.topic or "").strip()
        if not normalized_topic:
            raise ValueError("topic is required")

        if self.message_type == "rpc":
            if self.object_type != "target":
                raise ValueError("rpc only supports target object_type")
            if self.rpc_phase not in {"request", "response"}:
                raise ValueError("rpc requires rpc_phase=request|response")
            if self.target is None:
                raise ValueError("rpc target is required")
            if self.rpc_phase == "request":
                return self
            if not self.target.agent_id:
                raise ValueError("rpc response target must use target.agent_id")
            if not self.correlation_id:
                raise ValueError("rpc response requires correlation_id")
            if self.ok is None:
                raise ValueError("rpc response requires ok")
            return self

        if self.rpc_phase is not None:
            raise ValueError("event does not support rpc_phase")
        if self.object_type == "broadcast":
            if self.target is not None:
                raise ValueError("broadcast does not support target")
            return self
        if self.target is None:
            raise ValueError("target event requires target")
        return self

    @property
    def kind(self) -> str:
        if self.message_type == "event":
            return "event"
        if self.rpc_phase == "response":
            return "rpc_response"
        return "rpc_request"

    @property
    def delivery(self) -> str:
        return "broadcast" if self.object_type == "broadcast" else "unicast"

    @property
    def target_id(self) -> Optional[str]:
        if self.target is None:
            return None
        value = str(self.target.agent_id or "").strip()
        return value or None

    @property
    def target_profile(self) -> Optional[str]:
        if self.target is None:
            return None
        value = str(self.target.profile_id or "").strip()
        return value or None

    @classmethod
    def build_rpc_request(
        cls,
        *,
        topic: str,
        sender_id: str,
        target_id: Optional[str] = None,
        target_profile: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout_ms: int = 300_000,
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        message_id = uuid4().hex
        return cls(
            id=message_id,
            message_type="rpc",
            object_type="target",
            rpc_phase="request",
            topic=topic,
            sender_id=sender_id,
            target=_build_target_ref(target_id=target_id, target_profile=target_profile),
            correlation_id=message_id,
            run_id=run_id,
            session_id=session_id,
            timeout_ms=timeout_ms,
            visibility=visibility,
            level=level,
            payload=payload or {},
            metadata=metadata or {},
        )

    @classmethod
    def build_rpc_response(
        cls,
        *,
        request: "AgentMessage",
        sender_id: str,
        payload: Optional[Dict[str, Any]] = None,
        ok: bool,
        visibility: Optional[VisibilityLevel] = None,
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        return cls(
            message_type="rpc",
            object_type="target",
            rpc_phase="response",
            topic=request.topic,
            sender_id=sender_id,
            target=cls.TargetRef(agent_id=request.sender_id),
            correlation_id=request.correlation_id or request.id,
            run_id=request.run_id,
            session_id=request.session_id,
            visibility=visibility or request.visibility,
            level=level,
            ok=ok,
            payload=payload or {},
            metadata=metadata or {},
        )

    @classmethod
    def build_event(
        cls,
        *,
        topic: str,
        sender_id: str,
        payload: Optional[Dict[str, Any]] = None,
        target_id: Optional[str] = None,
        target_profile: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        delivery: AgentDelivery = "unicast",
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        return cls(
            message_type="event",
            object_type="broadcast" if delivery == "broadcast" else "target",
            topic=topic,
            sender_id=sender_id,
            target=(
                None
                if delivery == "broadcast"
                else _build_target_ref(target_id=target_id, target_profile=target_profile)
            ),
            run_id=run_id,
            session_id=session_id,
            visibility=visibility,
            level=level,
            payload=payload or {},
            metadata=metadata or {},
        )


def _build_target_ref(
    *,
    target_id: Optional[str] = None,
    target_profile: Optional[str] = None,
) -> AgentMessage.TargetRef:
    normalized_target_id = str(target_id or "").strip() or None
    normalized_target_profile = str(target_profile or "").strip() or None
    if normalized_target_id is not None:
        normalized_target_profile = None
    return AgentMessage.TargetRef(
        agent_id=normalized_target_id,
        profile_id=normalized_target_profile,
    )
