import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, List

from fastapi import WebSocket


@dataclass
class WsConnection:
    id: str
    websocket: WebSocket
    session_ids: Set[str] = field(default_factory=set)


class WsHub:
    def __init__(self) -> None:
        self._connections: Dict[str, WsConnection] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, websocket: WebSocket) -> WsConnection:
        conn = WsConnection(id=uuid.uuid4().hex[:12], websocket=websocket)
        async with self._lock:
            self._connections[conn.id] = conn
        return conn

    async def unregister(self, conn: Optional[WsConnection]) -> None:
        if not conn:
            return
        async with self._lock:
            self._connections.pop(conn.id, None)

    async def subscribe(self, conn: WsConnection, session_ids: List[str]) -> None:
        if not conn or not session_ids:
            return
        clean = {str(item) for item in session_ids if item}
        if not clean:
            return
        async with self._lock:
            existing = self._connections.get(conn.id)
            if not existing:
                return
            existing.session_ids.update(clean)

    async def unsubscribe(self, conn: WsConnection, session_ids: List[str]) -> None:
        if not conn or not session_ids:
            return
        clean = {str(item) for item in session_ids if item}
        if not clean:
            return
        async with self._lock:
            existing = self._connections.get(conn.id)
            if not existing:
                return
            existing.session_ids.difference_update(clean)

    async def emit(self, session_id: str, payload: Dict[str, Any]) -> None:
        if not session_id:
            return
        async with self._lock:
            candidates = list(self._connections.values())
        if not candidates:
            return

        to_remove: List[WsConnection] = []
        for conn in candidates:
            if session_id not in conn.session_ids:
                continue
            try:
                await conn.websocket.send_json(payload)
            except Exception:
                to_remove.append(conn)

        if to_remove:
            async with self._lock:
                for conn in to_remove:
                    self._connections.pop(conn.id, None)

    def emit_threadsafe(self, session_id: str, payload: Dict[str, Any]) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.emit(session_id, payload), loop)


_WS_HUB = WsHub()


def get_ws_hub() -> WsHub:
    return _WS_HUB
