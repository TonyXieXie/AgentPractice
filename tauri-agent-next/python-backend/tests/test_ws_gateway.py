from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_services import build_app_services
from main import create_app


class WsGatewayTests(unittest.TestCase):
    def test_resume_requires_single_run_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                with client.websocket_connect("/ws") as websocket:
                    connected = websocket.receive_json()
                    self.assertEqual(connected["kind"], "ack")
                    websocket.send_json({"kind": "resume", "after_seq": 0})
                    error = websocket.receive_json()
                    self.assertEqual(error["kind"], "error")

    def test_ws_receives_live_chunks_and_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                with client.websocket_connect("/ws") as websocket:
                    connected = websocket.receive_json()
                    self.assertEqual(connected["kind"], "ack")

                    create_response = client.post("/runs", json={"content": "hello from ws"})
                    self.assertEqual(create_response.status_code, 200)
                    run_id = create_response.json()["run_id"]

                    live_chunks = []
                    while True:
                        frame = websocket.receive_json()
                        if frame["kind"] != "chunk" or frame.get("run_id") != run_id:
                            continue
                        live_chunks.append(frame)
                        if frame.get("done") and frame.get("event_type") == "run.finished":
                            break

                    self.assertTrue(any(chunk["stream"] == "llm_chunk" for chunk in live_chunks))
                    self.assertTrue(any(chunk["event_type"] == "run.finished" for chunk in live_chunks))

                    websocket.send_json(
                        {
                            "kind": "set_scope",
                            "scopes": [{"run_id": run_id}],
                        }
                    )
                    scope_ack = self._receive_until(
                        websocket,
                        lambda frame: frame["kind"] == "ack" and frame.get("message") == "set_scope",
                    )
                    self.assertEqual(scope_ack["kind"], "ack")
                    self.assertEqual(scope_ack["message"], "set_scope")

                    websocket.send_json({"kind": "resume", "after_seq": 0})
                    replayed = []
                    while True:
                        frame = websocket.receive_json()
                        if frame["kind"] == "ack" and frame.get("message") == "resume":
                            resume_ack = frame
                            break
                        replayed.append(frame)

                    self.assertTrue(replayed)
                    self.assertEqual(replayed[0]["kind"], "chunk")
                    self.assertEqual(replayed[0]["run_id"], run_id)
                    self.assertGreaterEqual(resume_ack["payload"]["replayed"], len(replayed))

    def _receive_until(self, websocket, predicate):
        while True:
            frame = websocket.receive_json()
            if predicate(frame):
                return frame
