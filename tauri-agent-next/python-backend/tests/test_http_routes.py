from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_services import build_app_services
from main import create_app


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

                snapshot_response = self._wait_for_snapshot(client, run_id)
                self.assertEqual(snapshot_response.status_code, 200)
                snapshot_payload = snapshot_response.json()
                self.assertEqual(snapshot_payload["snapshot"]["status"], "completed")
                self.assertEqual(snapshot_payload["run_projection"]["status"], "completed")

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
