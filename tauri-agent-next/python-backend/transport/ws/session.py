from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from fastapi import WebSocket


@dataclass
class WsSession:
    websocket: WebSocket
    ws_session_id: str = ""
    viewer_id: Optional[str] = None
    target_session_id: Optional[str] = None
    selected_run_id: Optional[str] = None
    selected_agent_id: Optional[str] = None
    include_private: bool = False
    shared_after_seq: int = 0
    private_after_id: int = 0

    def __post_init__(self) -> None:
        if not self.ws_session_id:
            self.ws_session_id = uuid4().hex[:12]
