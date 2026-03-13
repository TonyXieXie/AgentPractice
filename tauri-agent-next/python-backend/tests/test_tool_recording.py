from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.execution.directives import MESSAGE_DIRECTIVE_KINDS
from agents.execution.tool_executor import ToolExecutor
from agents.execution.tool_recorder import PrivateExecutionRecorder
from observation.center import ObservationCenter
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore
from tools.base import Tool, ToolParameter, ToolRegistry


class EchoTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "echo_record"
        self.description = "Return the supplied text."
        self.parameters = [
            ToolParameter(name="text", type="string", description="Text to echo")
        ]

    async def execute(self, arguments):
        return arguments["text"]


class ReservedFinishTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "finish_run"
        self.description = "A conflicting custom tool for testing."
        self.parameters = []

    async def execute(self, arguments):
        return {"unexpected": arguments}


class ToolRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.shared_repo = SharedFactRepository(self.store)
        self.private_repo = AgentPrivateEventRepository(self.store)
        self.observation_center = ObservationCenter(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
        )
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)
        ToolRegistry.clear()
        ToolRegistry.register(EchoTool())

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()
        self._temp_dir.cleanup()

    async def test_tool_calls_are_recorded_as_private_events(self) -> None:
        tool_executor = ToolExecutor(
            recorder=PrivateExecutionRecorder(
                self.observation_center,
                max_payload_bytes=64 * 1024,
            )
        )
        trigger_fact = await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            run_id="run-1",
            sender_id="UserProxy",
            target_agent_id="assistant-1",
            topic="task.run",
            fact_type="rpc_request",
            payload_json={"content": "hello"},
        )
        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            work_path=None,
            metadata={"task_id": "task-1", "trigger_fact_id": trigger_fact.fact_id},
            tool_name="echo_record",
            arguments={"text": "hello"},
            tool_call_id="call-1",
        )
        self.assertTrue(result.ok)

        events = await self.private_repo.list(
            self.session_id,
            owner_agent_id="assistant-1",
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual([event.trigger_fact_id for event in events], [trigger_fact.fact_id] * 2)
        self.assertEqual([event.task_id for event in events], ["task-1", "task-1"])

    async def test_reserved_directive_tool_names_cannot_be_overridden(self) -> None:
        ToolRegistry.clear()
        ToolRegistry.register(ReservedFinishTool())
        tool_executor = ToolExecutor(allowed_builtin_tool_names=MESSAGE_DIRECTIVE_KINDS)

        tool_names = {tool.name for tool in tool_executor.list_tools()}
        self.assertNotIn("finish_run", tool_names)

        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            work_path=None,
            metadata={},
            tool_name="finish_run",
            arguments={},
            tool_call_id="call-2",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Tool not found: finish_run")
