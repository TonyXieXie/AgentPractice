from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Protocol

from observation.events import ExecutionEvent, ExecutionSnapshot


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
        self._seq = 0
        self._lock = asyncio.Lock()

    async def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        async with self._lock:
            self._seq += 1
            if event.seq is None:
                event = event.model_copy(update={"seq": self._seq})
            self._events.append(event)
            if event.run_id:
                snapshot = self._snapshots.get(event.run_id) or ExecutionSnapshot(
                    run_id=event.run_id
                )
                self._snapshots[event.run_id] = snapshot.apply(event)
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
