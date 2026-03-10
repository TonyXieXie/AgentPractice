from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_services import build_app_services
from main import create_app
from tools.base import Tool, ToolParameter, ToolRegistry


class SleepTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "sleep"
        self.description = "Sleep for N seconds (testing tool)."
        self.parameters = [
            ToolParameter(name="seconds", type="number", description="Seconds to sleep"),
        ]

    async def execute(self, arguments):
        import asyncio

        await asyncio.sleep(float(arguments["seconds"]))
        return {"slept": float(arguments["seconds"])}


class HttpRouteTests(unittest.TestCase):
    def test_observe_page_and_static_assets_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                page_response = client.get("/observe")
                self.assertEqual(page_response.status_code, 200)
                self.assertIn("Minimal Run Observation", page_response.text)
                self.assertIn("/static/observe/observe.js", page_response.text)
                self.assertIn("/static/observe/observe.css", page_response.text)

                js_response = client.get("/static/observe/observe.js")
                self.assertEqual(js_response.status_code, 200)
                self.assertIn("loadRun", js_response.text)

                css_response = client.get("/static/observe/observe.css")
                self.assertEqual(css_response.status_code, 200)
                self.assertIn(".layout-grid", css_response.text)

    def test_create_run_and_query_snapshot_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                response = client.post("/runs", json={"content": "hello from http"})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "accepted")
                run_id = payload["run_id"]
                session_id = payload["session_id"]
                self.assertTrue(session_id)
                first_user_agent_id = payload["user_agent_id"]
                first_assistant_agent_id = payload["assistant_agent_id"]
                self.assertTrue(first_user_agent_id)
                self.assertTrue(first_assistant_agent_id)

                snapshot_response = self._wait_for_snapshot(client, run_id)
                self.assertEqual(snapshot_response.status_code, 200)
                snapshot_payload = snapshot_response.json()
                self.assertEqual(snapshot_payload["snapshot"]["status"], "completed")
                self.assertEqual(snapshot_payload["run_projection"]["status"], "completed")

                followup = client.post(
                    "/runs",
                    json={"content": "second message", "session_id": session_id},
                )
                self.assertEqual(followup.status_code, 200)
                followup_payload = followup.json()
                self.assertEqual(followup_payload["session_id"], session_id)
                self.assertEqual(followup_payload["user_agent_id"], first_user_agent_id)
                self.assertEqual(followup_payload["assistant_agent_id"], first_assistant_agent_id)

                events_response = client.get(
                    f"/runs/{run_id}/events",
                    params={"after_seq": 0, "limit": 50},
                )
                self.assertEqual(events_response.status_code, 200)
                events_payload = events_response.json()
                event_types = [event["event_type"] for event in events_payload["events"]]
                self.assertIn("run.started", event_types)
                self.assertIn("run.finished", event_types)
                self.assertGreaterEqual(events_payload["next_after_seq"], 1)

    def test_session_busy_returns_409(self) -> None:
        ToolRegistry.clear()
        ToolRegistry.register(SleepTool())
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
                with TestClient(app) as client:
                    slow = client.post(
                        "/runs",
                        json={
                            "content": "slow tool run",
                            "request_overrides": {
                                "tool_name": "sleep",
                                "tool_arguments": {"seconds": 0.25},
                            },
                        },
                    )
                    self.assertEqual(slow.status_code, 200)
                    slow_payload = slow.json()
                    session_id = slow_payload["session_id"]
                    run_id = slow_payload["run_id"]

                    busy = client.post(
                        "/runs",
                        json={"content": "should be busy", "session_id": session_id},
                    )
                    self.assertEqual(busy.status_code, 409)
                    detail = busy.json().get("detail") or {}
                    self.assertEqual(detail.get("session_id"), session_id)
                    self.assertEqual(detail.get("active_run_id"), run_id)

                    snapshot_response = self._wait_for_snapshot(client, run_id)
                    self.assertEqual(snapshot_response.status_code, 200)
        finally:
            ToolRegistry.clear()

    def _wait_for_snapshot(self, client: TestClient, run_id: str):
        deadline = time.time() + 2.0
        last_response = None
        while time.time() < deadline:
            last_response = client.get(f"/runs/{run_id}/snapshot")
            if last_response.status_code == 200:
                status = last_response.json()["snapshot"]["status"]
                if status in {"completed", "error", "stopped"}:
                    return last_response
            time.sleep(0.02)
        return last_response
