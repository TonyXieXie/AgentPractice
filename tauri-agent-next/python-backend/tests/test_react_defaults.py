from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app_config import set_app_config_path
from agents.execution.engine import ExecutionEngine
from agents.execution.react_strategy import ReactStrategy
from agents.message import AgentMessage


class ReactDefaultTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_app_config_path(None)

    def test_react_strategy_uses_configured_max_iterations_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "app_config.json"
            config_path.write_text(
                json.dumps({"agent": {"react_max_iterations": 7}}),
                encoding="utf-8",
            )
            set_app_config_path(config_path)

            strategy = ReactStrategy()

        self.assertEqual(strategy.max_iterations, 7)

    def test_execution_engine_defaults_to_react_strategy(self) -> None:
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

        strategy = engine._resolve_strategy(message)

        self.assertEqual(strategy.name, "react")

    def test_execution_engine_unknown_strategy_falls_back_to_react(self) -> None:
        agent = SimpleNamespace(
            agent_id="agent-1",
            instance=SimpleNamespace(agent_type="assistant", role="assistant"),
        )
        engine = ExecutionEngine(agent)
        message = AgentMessage.build_event(
            topic="task.run",
            sender_id="external:http",
            target_id="agent-1",
            payload={"content": "hello", "strategy": "unknown"},
            session_id="session-1",
            run_id="run-1",
        )

        strategy = engine._resolve_strategy(message)

        self.assertEqual(strategy.name, "react")
