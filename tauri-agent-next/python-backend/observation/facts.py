from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agents.message import SeverityLevel, VisibilityLevel, utc_now_iso


def _coerce_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


class SharedFact(BaseModel):
    fact_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    run_id: Optional[str] = None
    fact_seq: int = 0
    message_id: Optional[str] = None
    sender_id: str
    target_agent_id: Optional[str] = None
    target_profile_id: Optional[str] = None
    topic: str
    fact_type: str
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    visibility: VisibilityLevel = "public"
    level: SeverityLevel = "info"
    created_at: str = Field(default_factory=utc_now_iso)

    @property
    def payload(self) -> Dict[str, Any]:
        return dict(self.payload_json or {})

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self.metadata_json or {})


class PrivateExecutionEvent(BaseModel):
    private_event_id: int = 0
    session_id: str
    owner_agent_id: str
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    trigger_fact_id: Optional[str] = None
    parent_private_event_id: Optional[int] = None
    kind: str
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)

    @property
    def payload(self) -> Dict[str, Any]:
        return dict(self.payload_json or {})


class ObservationScope(BaseModel):
    session_id: str
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    include_private: bool = False
