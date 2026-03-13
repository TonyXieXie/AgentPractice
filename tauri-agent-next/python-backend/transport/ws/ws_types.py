from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class WsSetScopeMessage(BaseModel):
    kind: Literal["set_scope"] = "set_scope"
    viewer_id: Optional[str] = None
    target_session_id: str
    selected_run_id: Optional[str] = None
    selected_agent_id: Optional[str] = None
    include_private: bool = False


class WsRequestBootstrapMessage(BaseModel):
    kind: Literal["request_bootstrap"] = "request_bootstrap"
    shared_limit: int = Field(default=200, ge=1, le=1000)
    private_limit: int = Field(default=200, ge=1, le=1000)


class WsResumeSharedMessage(BaseModel):
    kind: Literal["resume_shared"] = "resume_shared"
    after_seq: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class WsResumePrivateMessage(BaseModel):
    kind: Literal["resume_private"] = "resume_private"
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class WsHeartbeatMessage(BaseModel):
    kind: Literal["heartbeat"] = "heartbeat"


class WsAckFrame(BaseModel):
    kind: Literal["ack"] = "ack"
    ws_session_id: Optional[str] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsErrorFrame(BaseModel):
    kind: Literal["error"] = "error"
    ws_session_id: Optional[str] = None
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsHeartbeatFrame(BaseModel):
    kind: Literal["heartbeat"] = "heartbeat"
    ws_session_id: Optional[str] = None
    message: str = "alive"


class WsBootstrapSharedFactsFrame(BaseModel):
    kind: Literal["bootstrap.shared_facts"] = "bootstrap.shared_facts"
    ws_session_id: Optional[str] = None
    shared_facts: list[Dict[str, Any]] = Field(default_factory=list)


class WsBootstrapPrivateEventsFrame(BaseModel):
    kind: Literal["bootstrap.private_events"] = "bootstrap.private_events"
    ws_session_id: Optional[str] = None
    private_events: list[Dict[str, Any]] = Field(default_factory=list)


class WsBootstrapCursorsFrame(BaseModel):
    kind: Literal["bootstrap.cursors"] = "bootstrap.cursors"
    ws_session_id: Optional[str] = None
    shared_after_seq: int = 0
    private_after_id: int = 0


class WsAppendSharedFactFrame(BaseModel):
    kind: Literal["append.shared_fact"] = "append.shared_fact"
    ws_session_id: Optional[str] = None
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    shared_fact: Dict[str, Any]


class WsAppendPrivateEventFrame(BaseModel):
    kind: Literal["append.private_event"] = "append.private_event"
    ws_session_id: Optional[str] = None
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    private_event: Dict[str, Any]
