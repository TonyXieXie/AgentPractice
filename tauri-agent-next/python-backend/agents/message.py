from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


AgentMessageKind = Literal["rpc_request", "rpc_response", "event"]
AgentDelivery = Literal["unicast", "broadcast"]
VisibilityLevel = Literal["public", "internal", "debug"]
SeverityLevel = Literal["debug", "info", "warning", "error"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: AgentMessageKind
    delivery: AgentDelivery = "unicast"
    topic: str
    sender_id: str
    target_id: Optional[str] = None
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
        if self.kind in {"rpc_request", "rpc_response"} and self.delivery != "unicast":
            raise ValueError("RPC messages must use unicast delivery")
        if self.kind == "rpc_request" and not self.target_id:
            raise ValueError("rpc_request requires target_id")
        if self.kind == "rpc_response":
            if not self.target_id:
                raise ValueError("rpc_response requires target_id")
            if not self.correlation_id:
                raise ValueError("rpc_response requires correlation_id")
            if self.ok is None:
                raise ValueError("rpc_response requires ok")
        if self.kind == "event" and self.delivery == "unicast" and not self.target_id:
            raise ValueError("unicast event requires target_id")
        return self

    @classmethod
    def build_rpc_request(
        cls,
        *,
        topic: str,
        sender_id: str,
        target_id: str,
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
            kind="rpc_request",
            delivery="unicast",
            topic=topic,
            sender_id=sender_id,
            target_id=target_id,
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
            kind="rpc_response",
            delivery="unicast",
            topic=request.topic,
            sender_id=sender_id,
            target_id=request.sender_id,
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
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        delivery: AgentDelivery = "unicast",
        visibility: VisibilityLevel = "public",
        level: SeverityLevel = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        return cls(
            kind="event",
            delivery=delivery,
            topic=topic,
            sender_id=sender_id,
            target_id=target_id,
            run_id=run_id,
            session_id=session_id,
            visibility=visibility,
            level=level,
            payload=payload or {},
            metadata=metadata or {},
        )
