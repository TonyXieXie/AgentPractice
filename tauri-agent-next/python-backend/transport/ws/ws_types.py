from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


StreamKind = Literal[
    "run_event",
    "agent_event",
    "tool_chunk",
    "llm_chunk",
    "snapshot_patch",
]


class SubscriptionScope(BaseModel):
    run_id: Optional[str] = None
    agent_id: Optional[str] = None

    def matches(self, run_id: Optional[str], agent_id: Optional[str]) -> bool:
        if self.run_id and self.run_id != run_id:
            return False
        if self.agent_id and self.agent_id != agent_id:
            return False
        return True


class WsInboundMessage(BaseModel):
    kind: Literal["subscribe", "unsubscribe", "set_scope", "resume", "heartbeat"]
    scopes: List[SubscriptionScope] = Field(default_factory=list)
    after_seq: Optional[int] = None


class WSChunk(BaseModel):
    kind: Literal["chunk"] = "chunk"
    stream: StreamKind
    seq: int
    chunk_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    done: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsAckFrame(BaseModel):
    kind: Literal["ack"] = "ack"
    connection_id: Optional[str] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsErrorFrame(BaseModel):
    kind: Literal["error"] = "error"
    connection_id: Optional[str] = None
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsHeartbeatFrame(BaseModel):
    kind: Literal["heartbeat"] = "heartbeat"
    connection_id: Optional[str] = None
    message: str = "alive"
