from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_services import build_app_services
from main import create_app
from repositories.session_repository import SessionRepository


class WsGatewayTests(unittest.TestCase):
    def test_set_scope_and_request_bootstrap_return_raw_fact_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                response = client.post(
                    "/runs",
                    json={
                        "content": "bootstrap me",
                        "request_overrides": {
                            "tool_name": "finish_run",
                            "tool_arguments": {"reply": "done"},
                        },
                    },
                )
                accepted = response.json()
                session_id = accepted["session_id"]
                user_agent_id = accepted["user_agent_id"]
                self._wait_for_http_completion(client, session_id)

                with client.websocket_connect("/ws") as websocket:
                    connected = websocket.receive_json()
                    self.assertEqual(connected["kind"], "ack")
                    websocket.send_json(
                        {
                            "kind": "set_scope",
                            "target_session_id": session_id,
                            "selected_agent_id": user_agent_id,
                            "include_private": True,
                        }
                    )
                    self.assertEqual(websocket.receive_json()["message"], "set_scope")
                    websocket.send_json({"kind": "request_bootstrap"})

                    shared_frame = websocket.receive_json()
                    private_frame = websocket.receive_json()
                    cursor_frame = websocket.receive_json()
                    ack_frame = websocket.receive_json()

                    self.assertEqual(shared_frame["kind"], "bootstrap.shared_facts")
                    self.assertEqual(private_frame["kind"], "bootstrap.private_events")
                    self.assertEqual(cursor_frame["kind"], "bootstrap.cursors")
                    self.assertEqual(ack_frame["kind"], "ack")
                    self.assertEqual(ack_frame["message"], "request_bootstrap")
                    self.assertTrue(shared_frame["shared_facts"])
                    self.assertEqual(
                        [event["kind"] for event in private_frame["private_events"]],
                        ["tool_call", "tool_result"],
                    )

                    websocket.send_json({"kind": "resume_shared", "after_seq": 0})
                    resumed_shared = []
                    while True:
                        frame = websocket.receive_json()
                        if frame["kind"] == "ack" and frame["message"] == "resume_shared":
                            break
                        resumed_shared.append(frame)
                    self.assertTrue(all(frame["kind"] == "append.shared_fact" for frame in resumed_shared))

                    websocket.send_json({"kind": "resume_private", "after_id": 0})
                    resumed_private = []
                    while True:
                        frame = websocket.receive_json()
                        if frame["kind"] == "ack" and frame["message"] == "resume_private":
                            break
                        resumed_private.append(frame)
                    self.assertTrue(all(frame["kind"] == "append.private_event" for frame in resumed_private))

    def test_live_append_frames_fan_out_after_scope_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            services = build_app_services(data_dir=Path(temp_dir))
            app = create_app(services=services)
            session_repo = SessionRepository(services.sqlite_store)
            session_id = "session-live"
            with TestClient(app) as client:
                import asyncio

                asyncio.run(session_repo.create(session_id=session_id))
                with client.websocket_connect("/ws") as websocket:
                    websocket.receive_json()
                    websocket.send_json(
                        {
                            "kind": "set_scope",
                            "target_session_id": session_id,
                            "include_private": True,
                        }
                    )
                    websocket.receive_json()

                    response = client.post(
                        "/runs",
                        json={
                            "content": "live stream",
                            "session_id": session_id,
                            "request_overrides": {
                                "tool_name": "finish_run",
                                "tool_arguments": {"reply": "done"},
                            },
                        },
                    )
                    accepted = response.json()
                    user_agent_id = accepted["user_agent_id"]

                    frames = []
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        frame = websocket.receive_json()
                        frames.append(frame)
                        if frame["kind"] == "append.shared_fact" and frame["shared_fact"]["topic"] == "run.finished":
                            break

                    self.assertTrue(any(frame["kind"] == "append.shared_fact" for frame in frames))

                    websocket.send_json(
                        {
                            "kind": "set_scope",
                            "target_session_id": session_id,
                            "selected_agent_id": user_agent_id,
                            "include_private": True,
                        }
                    )
                    websocket.receive_json()
                    websocket.send_json({"kind": "resume_private", "after_id": 0})
                    private_frames = []
                    while True:
                        frame = websocket.receive_json()
                        if frame["kind"] == "ack" and frame["message"] == "resume_private":
                            break
                        private_frames.append(frame)
                    self.assertTrue(any(frame["kind"] == "append.private_event" for frame in private_frames))

    def _wait_for_http_completion(self, client: TestClient, session_id: str) -> None:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            payload = client.get(f"/sessions/{session_id}/facts/shared").json()
            if any(item["topic"] == "run.finished" for item in payload["shared_facts"]):
                return
            time.sleep(0.05)
