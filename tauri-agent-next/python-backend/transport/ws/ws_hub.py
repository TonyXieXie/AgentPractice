from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import WebSocket

from transport.ws.ws_types import SubscriptionScope, WSChunk


@dataclass(frozen=True, slots=True)
class ScopeRef:
    run_id: Optional[str] = None
    agent_id: Optional[str] = None

    @classmethod
    def from_scope(cls, scope: SubscriptionScope) -> "ScopeRef":
        return cls(run_id=scope.run_id, agent_id=scope.agent_id)

    def matches(self, run_id: Optional[str], agent_id: Optional[str]) -> bool:
        if self.run_id and self.run_id != run_id:
            return False
        if self.agent_id and self.agent_id != agent_id:
            return False
        return True


WILDCARD_SCOPE = ScopeRef()


@dataclass
class WsConnection:
    id: str
    websocket: WebSocket
    scopes: Set[ScopeRef] = field(default_factory=lambda: {WILDCARD_SCOPE})


class WsHub:
    def __init__(self) -> None:
        self._connections: Dict[str, WsConnection] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._seq = 0

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

    async def emit(
        self,
        *,
        stream: str,
        payload: Dict[str, Any],
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        done: bool = False,
    ) -> int:
        async with self._lock:
            self._seq += 1
            seq = self._seq
            candidates = list(self._connections.values())
        if not candidates:
            return seq

        outbound = WSChunk(
            seq=seq,
            stream=stream,
            run_id=run_id,
            agent_id=agent_id,
            done=done,
            payload=payload,
        ).model_dump()

        to_remove: List[WsConnection] = []
        for connection in candidates:
            if not self._matches_any_scope(connection.scopes, run_id, agent_id):
                continue
            try:
                await connection.websocket.send_json(outbound)
            except Exception:
                to_remove.append(connection)

        if to_remove:
            async with self._lock:
                for connection in to_remove:
                    self._connections.pop(connection.id, None)
        return seq

    def emit_threadsafe(
        self,
        *,
        stream: str,
        payload: Dict[str, Any],
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        done: bool = False,
    ) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            self.emit(
                stream=stream,
                payload=payload,
                run_id=run_id,
                agent_id=agent_id,
                done=done,
            ),
            loop,
        )

    def _matches_any_scope(
        self, scopes: Set[ScopeRef], run_id: Optional[str], agent_id: Optional[str]
    ) -> bool:
        return any(scope.matches(run_id, agent_id) for scope in scopes)


_WS_HUB = WsHub()


def get_ws_hub() -> WsHub:
    return _WS_HUB
