from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.execution.engine import ExecutionEngine
from agents.message import AgentMessage
from llm.default_config import clear_default_llm_config_cache, get_default_llm_config


class DefaultLlmConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("TAURI_AGENT_NEXT_DEMO_DB_PATH", None)
        clear_default_llm_config_cache()

    def test_get_default_llm_config_reads_default_row_from_demo_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat_app.db"
            self._seed_demo_llm_db(db_path)

            os.environ["TAURI_AGENT_NEXT_DEMO_DB_PATH"] = str(db_path)
            clear_default_llm_config_cache()

            config = get_default_llm_config()

        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config["name"], "Demo Default")
        self.assertEqual(config["model"], "gpt-5.2")
        self.assertEqual(config["api_format"], "openai_chat_completions")
        self.assertEqual(config["api_profile"], "openai")
        self.assertEqual(config["base_url"], "https://example.invalid/v1")

    def test_execution_engine_uses_demo_default_llm_config_when_message_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat_app.db"
            self._seed_demo_llm_db(db_path)

            os.environ["TAURI_AGENT_NEXT_DEMO_DB_PATH"] = str(db_path)
            clear_default_llm_config_cache()

            agent = SimpleNamespace(
                agent_id="agent-1",
                instance=SimpleNamespace(agent_type="assistant", role="assistant"),
            )
            engine = ExecutionEngine(agent)
            message = AgentMessage.build_event(
                topic="task.run",
                sender_id="external:http",
                target_id="agent-1",
                payload={"content": "hello"},
                session_id="session-1",
                run_id="run-1",
            )

            sentinel = object()
            with patch("llm.client.create_llm_client", return_value=sentinel) as create_client:
                resolved = engine._build_llm_client(message)

        self.assertIs(resolved, sentinel)
        create_client.assert_called_once()
        passed_config = create_client.call_args.args[0]
        self.assertEqual(passed_config.model, "gpt-5.2")
        self.assertEqual(passed_config.api_key, "demo-key")

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
                    "cfg-other",
                    "Other",
                    "openai",
                    "openai_chat_completions",
                    "openai",
                    "other-key",
                    "https://example.invalid/v1",
                    "gpt-5.4",
                    0.7,
                    20000,
                    200000,
                    0,
                    "high",
                    "detailed",
                    "2026-03-09T09:39:36.603423",
                ),
            )
            connection.commit()
        finally:
            connection.close()
