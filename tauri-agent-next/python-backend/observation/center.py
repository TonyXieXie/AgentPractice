from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from observation.events import ExecutionEvent, ExecutionProjectionState, ExecutionSnapshot
from observation.observer import ExecutionObserver
from observation.projection_builder import ProjectionBuilder
from observation.router import SubscriptionRouter
from observation.snapshot_builder import SnapshotBuilder
from repositories.event_repository import EventStore
from transport.ws.ws_hub import ScopeRef, WsConnection, WsHub
from transport.ws.ws_types import SubscriptionScope, WSChunk


class ObservationCenter(ExecutionObserver):
    def __init__(
        self,
        *,
        event_store: EventStore,
        ws_hub: WsHub,
        subscription_router: Optional[SubscriptionRouter] = None,
        snapshot_builder: Optional[SnapshotBuilder] = None,
        projection_builder: Optional[ProjectionBuilder] = None,
    ) -> None:
        self.event_store = event_store
        self.ws_hub = ws_hub
        self.subscription_router = subscription_router or SubscriptionRouter()
        self.snapshot_builder = snapshot_builder or SnapshotBuilder()
        self.projection_builder = projection_builder or ProjectionBuilder()
        self._lock = asyncio.Lock()
        self._latest_seq_by_run: Dict[str, int] = {}
        self._snapshots: Dict[str, ExecutionSnapshot] = {}
        self._projections: Dict[str, ExecutionProjectionState] = {}

    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        chunk: Optional[WSChunk] = None
        async with self._lock:
            event = await self._assign_seq_locked(event)
            if event.run_id:
                snapshot = await self._load_snapshot_locked(event.run_id, create_if_missing=True)
                updated_snapshot = self.snapshot_builder.apply(snapshot, event)
                self._snapshots[event.run_id] = updated_snapshot
                self._projections[event.run_id] = self.projection_builder.build(updated_snapshot)
                await self.event_store.append(event)
                await self.event_store.save_snapshot(event.run_id, updated_snapshot)
                chunk = self._build_chunk(event)
        if chunk is not None:
            await self.ws_hub.emit_chunk(chunk)
        return event

    async def get_snapshot(self, run_id: str) -> Optional[ExecutionSnapshot]:
        async with self._lock:
            return await self._load_snapshot_locked(run_id, create_if_missing=False)

    async def get_projection_state(self, run_id: str) -> ExecutionProjectionState:
        snapshot = await self.get_snapshot(run_id)
        if snapshot is None:
            return ExecutionProjectionState()
        async with self._lock:
            projection = self._projections.get(run_id)
            if projection is None:
                projection = self.projection_builder.build(snapshot)
                self._projections[run_id] = projection
            return projection

    async def list_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
        agent_id: Optional[str] = None,
        visibility: Optional[str] = None,
        level: Optional[str] = None,
    ) -> List[ExecutionEvent]:
        return await self.event_store.list(
            run_id,
            after_seq=after_seq,
            limit=limit,
            agent_id=agent_id,
            visibility=visibility,
            level=level,
        )

    async def latest_seq(self, run_id: str) -> int:
        async with self._lock:
            if run_id in self._latest_seq_by_run:
                return self._latest_seq_by_run[run_id]
        return await self.event_store.latest_seq(run_id)

    async def replay_connection(
        self,
        connection: WsConnection,
        *,
        after_seq: int = 0,
        batch_size: int = 200,
    ) -> tuple[Optional[str], int]:
        scopes = self._scope_models(connection)
        run_id = self.subscription_router.resolve_resume_run_id(scopes)
        if run_id is None:
            return None, 0

        started = await self.ws_hub.begin_replay(connection)
        if not started:
            return None, 0

        replayed = 0
        cursor = max(0, after_seq)
        try:
            while True:
                batch = await self.event_store.list(
                    run_id,
                    after_seq=cursor,
                    limit=batch_size,
                )
                if not batch:
                    break
                for event in batch:
                    if not self.subscription_router.matches_any_event(scopes, event):
                        cursor = max(cursor, int(event.seq or 0))
                        continue
                    await self.ws_hub.send_chunk(connection, self._build_chunk(event))
                    replayed += 1
                    cursor = max(cursor, int(event.seq or 0))
                if len(batch) < batch_size:
                    break
        finally:
            buffered = await self.ws_hub.end_replay(connection)

        for chunk in buffered:
            await self.ws_hub.send_chunk(connection, chunk)
        return run_id, replayed

    async def _assign_seq_locked(self, event: ExecutionEvent) -> ExecutionEvent:
        if not event.run_id:
            return event if event.seq is not None else event.model_copy(update={"seq": 0})
        latest = self._latest_seq_by_run.get(event.run_id)
        if latest is None:
            latest = await self.event_store.latest_seq(event.run_id)
        if event.seq is None or int(event.seq) <= latest:
            latest += 1
            event = event.model_copy(update={"seq": latest})
        else:
            latest = int(event.seq)
        self._latest_seq_by_run[event.run_id] = latest
        return event

    async def _load_snapshot_locked(
        self,
        run_id: str,
        *,
        create_if_missing: bool,
    ) -> Optional[ExecutionSnapshot]:
        snapshot = self._snapshots.get(run_id)
        if snapshot is not None:
            return snapshot
        snapshot = await self.event_store.load_snapshot(run_id)
        if snapshot is None:
            if not create_if_missing:
                return None
            snapshot = ExecutionSnapshot(run_id=run_id)
        self._snapshots[run_id] = snapshot
        self._latest_seq_by_run[run_id] = max(
            self._latest_seq_by_run.get(run_id, 0),
            snapshot.latest_seq,
        )
        return snapshot

    def _build_chunk(self, event: ExecutionEvent) -> WSChunk:
        payload = dict(event.payload)
        payload.update(
            {
                "event_type": event.event_type,
                "message_id": event.message_id,
                "tool_call_id": event.tool_call_id,
                "source_type": event.source_type,
                "source_id": event.source_id,
                "metadata": dict(event.metadata),
                "created_at": event.created_at,
                "tags": list(event.tags),
            }
        )
        return WSChunk(
            stream=self._stream_for_event(event),
            seq=int(event.seq or 0),
            run_id=event.run_id,
            agent_id=event.agent_id,
            message_id=event.message_id,
            tool_call_id=event.tool_call_id,
            event_type=event.event_type,
            source_type=event.source_type,
            source_id=event.source_id,
            visibility=event.visibility,
            level=event.level,
            tags=list(event.tags),
            done=self._is_terminal(event),
            payload=payload,
        )

    def _scope_models(self, connection: WsConnection) -> List[SubscriptionScope]:
        scopes = []
        for scope in connection.scopes:
            if isinstance(scope, ScopeRef):
                scopes.append(
                    SubscriptionScope(
                        run_id=scope.run_id,
                        agent_id=scope.agent_id,
                        visibility=scope.visibility,
                        level=scope.level,
                    )
                )
            else:
                scopes.append(scope)
        return scopes

    def _stream_for_event(self, event: ExecutionEvent) -> str:
        if event.event_type.startswith("run."):
            return "run_event"
        if event.event_type.startswith("tool."):
            return "tool_chunk"
        if event.event_type.startswith("llm."):
            return "llm_chunk"
        return "agent_event"

    def _is_terminal(self, event: ExecutionEvent) -> bool:
        return event.event_type in {"run.finished", "run.error"}
