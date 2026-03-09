from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from agents.message import SeverityLevel, VisibilityLevel
from observation.events import ExecutionEvent, ExecutionSnapshot, level_at_least
from runtime_paths import get_runs_data_dir


class EventStore(Protocol):
    async def append(self, event: ExecutionEvent) -> None:
        ...

    async def list(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
        agent_id: Optional[str] = None,
        visibility: Optional[VisibilityLevel] = None,
        level: Optional[SeverityLevel] = None,
    ) -> List[ExecutionEvent]:
        ...

    async def latest_seq(self, run_id: str) -> int:
        ...

    async def load_snapshot(self, run_id: str) -> Optional[ExecutionSnapshot]:
        ...

    async def save_snapshot(self, run_id: str, snapshot: ExecutionSnapshot) -> None:
        ...


class FileEventStore:
    def __init__(self, root_dir: Optional[Path] = None) -> None:
        self.root_dir = Path(root_dir or get_runs_data_dir()).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, asyncio.Lock] = {}

    async def append(self, event: ExecutionEvent) -> None:
        if not event.run_id:
            return
        payload = event.model_dump(mode="json")
        line = json.dumps(payload, ensure_ascii=True) + "\n"
        async with self._lock_for(event.run_id):
            events_path = self._events_path(event.run_id)
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    async def list(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
        agent_id: Optional[str] = None,
        visibility: Optional[VisibilityLevel] = None,
        level: Optional[SeverityLevel] = None,
    ) -> List[ExecutionEvent]:
        events_path = self._events_path(run_id)
        if not events_path.exists():
            return []
        results: List[ExecutionEvent] = []
        with events_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                event = ExecutionEvent.model_validate_json(line)
                if int(event.seq or 0) <= max(0, after_seq):
                    continue
                if agent_id and event.agent_id != agent_id:
                    continue
                if visibility and event.visibility != visibility:
                    continue
                if not level_at_least(event.level, level):
                    continue
                results.append(event)
                if len(results) >= max(1, limit):
                    break
        return results

    async def latest_seq(self, run_id: str) -> int:
        events_path = self._events_path(run_id)
        if not events_path.exists():
            return 0
        latest = 0
        with events_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                event = ExecutionEvent.model_validate_json(line)
                latest = max(latest, int(event.seq or 0))
        return latest

    async def load_snapshot(self, run_id: str) -> Optional[ExecutionSnapshot]:
        snapshot_path = self._snapshot_path(run_id)
        if not snapshot_path.exists():
            return None
        return ExecutionSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))

    async def save_snapshot(self, run_id: str, snapshot: ExecutionSnapshot) -> None:
        async with self._lock_for(run_id):
            snapshot_path = self._snapshot_path(run_id)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                snapshot.model_dump_json(indent=2),
                encoding="utf-8",
            )

    def _run_dir(self, run_id: str) -> Path:
        return self.root_dir / run_id

    def _events_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "events.jsonl"

    def _snapshot_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "snapshot.json"

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        lock = self._locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[run_id] = lock
        return lock
