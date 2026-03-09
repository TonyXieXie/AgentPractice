from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observation.center import ObservationCenter
from observation.events import ExecutionEvent
from repositories.event_repository import FileEventStore
from transport.ws.ws_hub import WsHub
from transport.ws.ws_types import SubscriptionScope


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class ObservationCenterTests(unittest.IsolatedAsyncioTestCase):
    async def test_assigns_per_run_seq_and_routes_by_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hub = WsHub()
            store = FileEventStore(Path(temp_dir) / "runs")
            center = ObservationCenter(event_store=store, ws_hub=hub)

            ws_run = FakeWebSocket()
            ws_internal = FakeWebSocket()
            conn_run = await hub.register(ws_run)
            conn_internal = await hub.register(ws_internal)
            await hub.set_scope(conn_run, [SubscriptionScope(run_id="run-1")])
            await hub.set_scope(
                conn_internal,
                [SubscriptionScope(run_id="run-1", visibility="internal", level="error")],
            )

            event_a = await center.emit(
                ExecutionEvent(
                    event_type="run.started",
                    run_id="run-1",
                    agent_id="assistant-1",
                )
            )
            event_b = await center.emit(
                ExecutionEvent(
                    event_type="run.started",
                    run_id="run-2",
                    agent_id="assistant-2",
                )
            )
            event_c = await center.emit(
                ExecutionEvent(
                    event_type="run.error",
                    run_id="run-1",
                    agent_id="assistant-1",
                    visibility="internal",
                    level="error",
                    payload={"status": "error", "error": "boom"},
                )
            )

            self.assertEqual(event_a.seq, 1)
            self.assertEqual(event_b.seq, 1)
            self.assertEqual(event_c.seq, 2)

            self.assertEqual(len(ws_run.messages), 2)
            self.assertEqual(len(ws_internal.messages), 1)
            self.assertEqual(ws_internal.messages[0]["event_type"], "run.error")

            snapshot = await center.get_snapshot("run-1")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.status, "error")
            self.assertEqual(snapshot.latest_seq, 2)

            projections = await center.get_projection_state("run-1")
            self.assertIsNotNone(projections.run_projection)
            self.assertEqual(projections.run_projection.latest_seq, 2)
