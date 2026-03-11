from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observation.events import ExecutionEvent, ExecutionSnapshot
from repositories.event_repository import FileEventStore


class EventStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_list_and_snapshot_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileEventStore(Path(temp_dir) / "runs")
            await store.append(
                ExecutionEvent(
                    event_type="run.started",
                    run_id="run-1",
                    agent_id="assistant-1",
                    seq=1,
                )
            )
            await store.append(
                ExecutionEvent(
                    event_type="run.error",
                    run_id="run-1",
                    agent_id="assistant-1",
                    seq=2,
                    visibility="internal",
                    level="error",
                )
            )
            await store.append(
                ExecutionEvent(
                    event_type="run.started",
                    run_id="run-2",
                    agent_id="assistant-2",
                    seq=1,
                )
            )

            filtered = await store.list(
                "run-1",
                after_seq=0,
                limit=10,
                visibility="internal",
                level="error",
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].event_type, "run.error")

            self.assertEqual(await store.latest_seq("run-1"), 2)

            snapshot = ExecutionSnapshot(run_id="run-1", status="completed", latest_seq=2)
            await store.save_snapshot("run-1", snapshot)
            loaded = await store.load_snapshot("run-1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(loaded.latest_seq, 2)
