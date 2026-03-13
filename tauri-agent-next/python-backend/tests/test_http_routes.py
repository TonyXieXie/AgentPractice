from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_config import set_app_config_path
from app_services import build_app_services
from main import create_app


class HttpRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_app_config_path(None)

    def test_observe_page_and_static_assets_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                page_response = client.get("/observe")
                self.assertEqual(page_response.status_code, 200)
                self.assertIn("/static/observe/observe.js", page_response.text)
                self.assertIn("/static/observe/observe.css", page_response.text)

    def test_config_endpoint_exposes_profile_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            config_path = runtime_dir / "app_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agent": {
                            "default_profile": "worker",
                            "profiles": {
                                "worker": {
                                    "extends": "default",
                                    "description": "Worker profile",
                                    "allowed_tool_names": ["send_event"],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            set_app_config_path(config_path)
            app = create_app(services=build_app_services(data_dir=runtime_dir))
            with TestClient(app) as client:
                response = client.get("/config")
                self.assertEqual(response.status_code, 200)
                config = response.json()["config"]

        default_profile = config["agent"]["profiles"]["default"]
        self.assertIn("description", default_profile)
        self.assertIn("allowed_tool_names", default_profile)
        self.assertIn("extends", default_profile)
        self.assertIn("editable", default_profile)
        self.assertEqual(config["agent"]["profiles"]["worker"]["description"], "Worker profile")

    def test_create_run_and_query_session_scoped_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                response = client.post(
                    "/runs",
                    json={
                        "content": "hello from http",
                        "request_overrides": {
                            "tool_name": "finish_run",
                            "tool_arguments": {"reply": "done"},
                        },
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                session_id = payload["session_id"]
                user_agent_id = payload["user_agent_id"]

                shared_payload = self._wait_for_shared_facts(client, session_id)
                topics = [fact["topic"] for fact in shared_payload["shared_facts"]]
                self.assertIn("run.submit", topics)
                self.assertIn("run.started", topics)
                self.assertIn("run.finished", topics)

                private_response = client.get(
                    f"/sessions/{session_id}/facts/private",
                    params={"agent_id": user_agent_id},
                )
                self.assertEqual(private_response.status_code, 200)
                private_payload = private_response.json()
                self.assertEqual(
                    [event["kind"] for event in private_payload["private_events"]],
                    ["tool_call", "tool_result"],
                )

                self.assertEqual(client.get(f"/runs/{payload['run_id']}/snapshot").status_code, 404)
                self.assertEqual(client.get(f"/runs/{payload['run_id']}/events").status_code, 404)

    def _wait_for_shared_facts(self, client: TestClient, session_id: str):
        deadline = time.time() + 3.0
        last_payload = None
        while time.time() < deadline:
            response = client.get(f"/sessions/{session_id}/facts/shared")
            if response.status_code == 200:
                last_payload = response.json()
                topics = [fact["topic"] for fact in last_payload["shared_facts"]]
                if "run.finished" in topics:
                    return last_payload
            time.sleep(0.05)
        return last_payload
