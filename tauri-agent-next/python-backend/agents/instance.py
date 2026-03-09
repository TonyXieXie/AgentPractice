from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from agents.message import utc_now_iso


AgentStatus = Literal["idle", "running", "waiting", "stopped", "error"]


class AgentInstance(BaseModel):
    id: str
    agent_type: str
    role: str
    status: AgentStatus = "idle"
    run_id: Optional[str] = None
    profile_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def with_status(self, status: AgentStatus, **metadata: Any) -> "AgentInstance":
        merged_metadata = dict(self.metadata)
        merged_metadata.update(metadata)
        return self.model_copy(
            update={
                "status": status,
                "metadata": merged_metadata,
                "updated_at": utc_now_iso(),
            }
        )
