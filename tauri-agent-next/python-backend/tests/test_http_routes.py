from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_config import set_app_config_path
from app_services import build_app_services
from llm.default_config import clear_default_llm_config_cache
from main import create_app


class HttpRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_app_config_path(None)
        os.environ.pop("TAURI_AGENT_NEXT_DEMO_DB_PATH", None)
        clear_default_llm_config_cache()

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
                started_fact = next(
                    fact for fact in shared_payload["shared_facts"] if fact["topic"] == "run.started"
                )
                self.assertEqual(started_fact["payload_json"]["strategy"], "react")

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

    def test_create_run_uses_demo_default_llm_config_without_persisting_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            demo_db_path = runtime_dir / "demo-chat.db"
            self._seed_demo_llm_db(demo_db_path)

            os.environ["TAURI_AGENT_NEXT_DEMO_DB_PATH"] = str(demo_db_path)
            clear_default_llm_config_cache()

            app = create_app(services=build_app_services(data_dir=runtime_dir))
            with TestClient(app) as client:
                with patch(
                    "llm.client.create_llm_client",
                    side_effect=lambda config: _FakeDirectiveLlmClient(config),
                ):
                    response = client.post("/runs", json={"content": "hello from http"})
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    session_id = payload["session_id"]

                    shared_payload = self._wait_for_shared_facts(client, session_id)
                    facts = shared_payload["shared_facts"]
                    self.assertTrue(facts)
                    finished_fact = next(
                        fact for fact in reversed(facts) if fact["topic"] == "run.finished"
                    )
                    self.assertEqual(finished_fact["payload_json"]["status"], "completed")
                    self.assertEqual(
                        finished_fact["payload_json"]["reply"],
                        "done via default llm",
                    )
                    submit_fact = next(
                        fact for fact in facts if fact["topic"] == "run.submit" and fact["sender_id"] == "external:http"
                    )
                    self.assertIsNone(submit_fact["payload_json"]["llm_config"])

    def test_latest_prompt_trace_endpoint_returns_agent_llm_request_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            app = create_app(services=build_app_services(data_dir=runtime_dir))
            with TestClient(app) as client:
                with patch(
                    "llm.client.create_llm_client",
                    side_effect=lambda config: _FakeDirectiveLlmClient(config),
                ):
                    response = client.post(
                        "/runs",
                        json={
                            "content": "hello from http",
                            "llm_config": {
                                "name": "Test Config",
                                "api_profile": "openai",
                                "api_format": "openai_chat_completions",
                                "api_key": "test-key",
                                "model": "gpt-test",
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    session_id = payload["session_id"]
                    user_agent_id = payload["user_agent_id"]
                    run_id = payload["run_id"]

                    self._wait_for_shared_facts(client, session_id)
                    trace_response = client.get(
                        f"/sessions/{session_id}/prompt-trace/latest",
                        params={"agent_id": user_agent_id, "run_id": run_id},
                    )
                    self.assertEqual(trace_response.status_code, 200)
                    trace_payload = trace_response.json()
                    trace = trace_payload["prompt_trace"]

                    self.assertIsNotNone(trace)
                    self.assertEqual(trace["agent_id"], user_agent_id)
                    self.assertEqual(trace["run_id"], run_id)
                    self.assertEqual(trace["llm_model"], "gpt-test")
                    self.assertGreaterEqual(trace["rendered_message_count"], 2)
                    self.assertTrue(
                        any(
                            "hello from http" in json.dumps(message, ensure_ascii=False)
                            for message in trace["request_messages"]
                        )
                    )

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

    def _seed_demo_llm_db(self, db_path: Path) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                CREATE TABLE llm_configs (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  api_type TEXT,
                  api_format TEXT,
                  api_profile TEXT,
                  api_key TEXT,
                  base_url TEXT,
                  model TEXT,
                  temperature REAL,
                  max_tokens INTEGER,
                  max_context_tokens INTEGER,
                  is_default INTEGER,
                  reasoning_effort TEXT,
                  reasoning_summary TEXT,
                  created_at TEXT
                )
                """.strip()
            )
            connection.execute(
                """
                INSERT INTO llm_configs (
                  id,
                  name,
                  api_type,
                  api_format,
                  api_profile,
                  api_key,
                  base_url,
                  model,
                  temperature,
                  max_tokens,
                  max_context_tokens,
                  is_default,
                  reasoning_effort,
                  reasoning_summary,
                  created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """.strip(),
                (
                    "cfg-default",
                    "Demo Default",
                    "openai",
                    "openai_chat_completions",
                    "openai",
                    "demo-key",
                    "https://example.invalid/v1",
                    "gpt-5.2",
                    0.7,
                    20000,
                    200000,
                    1,
                    "xhigh",
                    "detailed",
                    "2026-03-09T09:38:36.603423",
                ),
            )
            connection.commit()
        finally:
            connection.close()


class _FakeDirectiveLlmClient:
    def __init__(self, config) -> None:
        self.config = config

    async def chat_stream_events(self, prompt_ir, request_overrides=None):
        yield {
            "type": "done",
            "content": "",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "name": "finish_run",
                    "arguments": '{"reply":"done via default llm","status":"completed"}',
                }
            ],
        }
