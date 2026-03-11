from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Protocol

from observation.events import ExecutionEvent, ExecutionSnapshot
from observation.snapshot_builder import SnapshotBuilder


class ExecutionObserver(Protocol):
    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        ...


class NullExecutionObserver:
    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        return event


class InMemoryExecutionObserver:
    def __init__(self) -> None:
        self._events: List[ExecutionEvent] = []
        self._snapshots: Dict[str, ExecutionSnapshot] = {}
        self._seq_by_run: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._snapshot_builder = SnapshotBuilder()

    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        async with self._lock:
            if event.run_id and event.seq is None:
                next_seq = self._seq_by_run.get(event.run_id, 0) + 1
                self._seq_by_run[event.run_id] = next_seq
                event = event.model_copy(update={"seq": next_seq})
            self._events.append(event)
            if event.run_id:
                snapshot = self._snapshots.get(event.run_id) or ExecutionSnapshot(
                    run_id=event.run_id
                )
                self._snapshots[event.run_id] = self._snapshot_builder.apply(snapshot, event)
            return event

    def list_events(
        self,
        *,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[ExecutionEvent]:
        events = list(self._events)
        if run_id is not None:
            events = [event for event in events if event.run_id == run_id]
        if agent_id is not None:
            events = [event for event in events if event.agent_id == agent_id]
        return events

    def get_snapshot(self, run_id: str) -> Optional[ExecutionSnapshot]:
        return self._snapshots.get(run_id)
