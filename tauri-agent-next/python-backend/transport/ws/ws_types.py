from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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
    kind: Literal["subscribe", "unsubscribe", "set_scope", "heartbeat"]
    scopes: List[SubscriptionScope] = Field(default_factory=list)


class WsOutboundMessage(BaseModel):
    kind: Literal["ack", "chunk", "error", "heartbeat"]
    connection_id: Optional[str] = None
    message: Optional[str] = None
    seq: Optional[int] = None
    stream: Optional[str] = None
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    done: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)
