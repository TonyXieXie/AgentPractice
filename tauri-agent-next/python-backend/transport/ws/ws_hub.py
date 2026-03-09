from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from fastapi import WebSocket

from observation.events import level_at_least
from transport.ws.ws_types import SubscriptionScope, WSChunk


@dataclass(frozen=True, slots=True)
class ScopeRef:
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    visibility: Optional[str] = None
    level: Optional[str] = None

    @classmethod
    def from_scope(cls, scope: SubscriptionScope) -> "ScopeRef":
        return cls(
            run_id=scope.run_id,
            agent_id=scope.agent_id,
            visibility=scope.visibility,
            level=scope.level,
        )

    def matches(self, chunk: WSChunk) -> bool:
        if self.run_id and self.run_id != chunk.run_id:
            return False
        if self.agent_id and self.agent_id != chunk.agent_id:
            return False
        if self.visibility and self.visibility != chunk.visibility:
            return False
        if self.level and not level_at_least(chunk.level, self.level):
            return False
        return True


WILDCARD_SCOPE = ScopeRef()


@dataclass
class WsConnection:
    id: str
    websocket: WebSocket
    scopes: Set[ScopeRef] = field(default_factory=lambda: {WILDCARD_SCOPE})
    replaying: bool = False
    buffered_chunks: List[WSChunk] = field(default_factory=list)


class WsHub:
    def __init__(self) -> None:
        self._connections: dict[str, WsConnection] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, websocket: WebSocket) -> WsConnection:
        connection = WsConnection(id=uuid.uuid4().hex[:12], websocket=websocket)
        async with self._lock:
            self._connections[connection.id] = connection
        return connection

    async def unregister(self, connection: Optional[WsConnection]) -> None:
        if connection is None:
            return
        async with self._lock:
            self._connections.pop(connection.id, None)

    async def subscribe(
        self, connection: WsConnection, scopes: Iterable[SubscriptionScope]
    ) -> None:
        await self._mutate_scopes(connection, scopes, mode="add")

    async def unsubscribe(
        self, connection: WsConnection, scopes: Iterable[SubscriptionScope]
    ) -> None:
        await self._mutate_scopes(connection, scopes, mode="remove")

    async def set_scope(
        self, connection: WsConnection, scopes: Iterable[SubscriptionScope]
    ) -> None:
        await self._mutate_scopes(connection, scopes, mode="replace")

    async def emit_chunk(self, chunk: WSChunk) -> int:
        async with self._lock:
            candidates: List[WsConnection] = []
            for connection in self._connections.values():
                if not self._matches_any_scope(connection.scopes, chunk):
                    continue
                if connection.replaying:
                    connection.buffered_chunks.append(chunk)
                else:
                    candidates.append(connection)

        if not candidates:
            return 0

        delivered = 0
        failed: List[WsConnection] = []
        for connection in candidates:
            try:
                await connection.websocket.send_json(chunk.model_dump(mode="json"))
                delivered += 1
            except Exception:
                failed.append(connection)

        if failed:
            async with self._lock:
                for connection in failed:
                    self._connections.pop(connection.id, None)
        return delivered

    async def send_chunk(self, connection: WsConnection, chunk: WSChunk) -> None:
        try:
            await connection.websocket.send_json(chunk.model_dump(mode="json"))
        except Exception:
            await self.unregister(connection)

    async def begin_replay(self, connection: WsConnection) -> bool:
        async with self._lock:
            existing = self._connections.get(connection.id)
            if existing is None:
                return False
            existing.replaying = True
            existing.buffered_chunks = []
            return True

    async def end_replay(self, connection: WsConnection) -> List[WSChunk]:
        async with self._lock:
            existing = self._connections.get(connection.id)
            if existing is None:
                return []
            buffered = list(existing.buffered_chunks)
            existing.buffered_chunks = []
            existing.replaying = False
            return buffered

    def emit_chunk_threadsafe(self, chunk: WSChunk) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.emit_chunk(chunk), loop)

    async def _mutate_scopes(
        self,
        connection: WsConnection,
        scopes: Iterable[SubscriptionScope],
        mode: str,
    ) -> None:
        clean = {ScopeRef.from_scope(scope) for scope in scopes if scope}
        if not clean:
            clean = {WILDCARD_SCOPE}
        async with self._lock:
            existing = self._connections.get(connection.id)
            if not existing:
                return
            if mode == "replace":
                existing.scopes = clean
            elif mode == "add":
                if WILDCARD_SCOPE in existing.scopes:
                    existing.scopes = set(clean)
                else:
                    existing.scopes.update(clean)
            elif mode == "remove":
                existing.scopes.difference_update(clean)
                if not existing.scopes:
                    existing.scopes = {WILDCARD_SCOPE}

    def _matches_any_scope(self, scopes: Set[ScopeRef], chunk: WSChunk) -> bool:
        return any(scope.matches(chunk) for scope in scopes)


_WS_HUB = WsHub()


def get_ws_hub() -> WsHub:
    return _WS_HUB
