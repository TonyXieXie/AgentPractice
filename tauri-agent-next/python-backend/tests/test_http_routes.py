from __future__ import annotations

import asyncio
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
                events = events_payload["events"]
                event_types = [event["event_type"] for event in events]
                self.assertIn("run.started", event_types)
                self.assertIn("run.finished", event_types)
                self.assertEqual(event_types.count("run.started"), 1)
                self.assertEqual(event_types.count("run.finished"), 1)
                run_started = next(event for event in events if event["event_type"] == "run.started")
                run_finished = next(event for event in events if event["event_type"] == "run.finished")
                self.assertEqual(run_started["source_type"], "run_manager")
                self.assertEqual(run_finished["source_type"], "run_manager")
                self.assertGreaterEqual(events_payload["next_after_seq"], 1)
                tasks = asyncio.run(app.state.services.task_repository.list_by_run(run_id))
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0].status, "completed")
                self.assertEqual(tasks[0].result["status"], "completed")
                task_event = next(
                    event
                    for event in events
                    if event["event_type"] == "llm.updated"
                    and (event.get("metadata") or {}).get("task_id")
                )
                self.assertEqual(task_event["metadata"]["task_id"], tasks[0].id)

    def test_session_busy_returns_conflict(self) -> None:
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
                    detail = busy.json()["detail"]
                    self.assertEqual(detail.get("status"), "busy")
                    self.assertEqual(detail.get("session_id"), session_id)
                    self.assertEqual(detail.get("run_id"), run_id)

                    snapshot_response = self._wait_for_snapshot(client, run_id)
                    self.assertEqual(snapshot_response.status_code, 200)
        finally:
            ToolRegistry.clear()

    def test_stop_run_emits_stopped_terminal_event(self) -> None:
        ToolRegistry.clear()
        ToolRegistry.register(SleepTool())
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
                with TestClient(app) as client:
                    response = client.post(
                        "/runs",
                        json={
                            "content": "stop me",
                            "request_overrides": {
                                "tool_name": "sleep",
                                "tool_arguments": {"seconds": 0.5},
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    run_id = response.json()["run_id"]
                    running_tasks = self._wait_for_tasks(app, run_id)
                    self.assertEqual(len(running_tasks), 1)
                    self.assertEqual(running_tasks[0].status, "running")

                    stop_response = client.post(f"/runs/{run_id}/stop")
                    self.assertEqual(stop_response.status_code, 200)
                    self.assertEqual(stop_response.json()["status"], "stopping")

                    snapshot_response = self._wait_for_snapshot(client, run_id)
                    self.assertEqual(snapshot_response.status_code, 200)
                    self.assertEqual(snapshot_response.json()["snapshot"]["status"], "stopped")

                    events_response = client.get(
                        f"/runs/{run_id}/events",
                        params={"after_seq": 0, "limit": 50},
                    )
                    self.assertEqual(events_response.status_code, 200)
                    events = events_response.json()["events"]
                    run_finished = next(
                        event for event in events if event["event_type"] == "run.finished"
                    )
                    self.assertEqual(run_finished["source_type"], "run_manager")
                    self.assertEqual(run_finished["payload"]["status"], "stopped")
                    tasks = asyncio.run(app.state.services.task_repository.list_by_run(run_id))
                    self.assertEqual(len(tasks), 1)
                    self.assertEqual(tasks[0].status, "stopped")
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

    def _wait_for_tasks(self, app, run_id: str):
        deadline = time.time() + 2.0
        tasks = []
        while time.time() < deadline:
            tasks = asyncio.run(app.state.services.task_repository.list_by_run(run_id))
            if tasks:
                return tasks
            time.sleep(0.02)
        return tasks
