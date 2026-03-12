from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.execution.directives import MESSAGE_DIRECTIVE_KINDS
from agents.execution.tool_executor import ToolExecutor
from agents.execution.tool_recorder import ConversationToolRecorder
from repositories.conversation_repository import ConversationRepository
from repositories.session_repository import SessionRepository
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
        self.conversation_repo = ConversationRepository(self.store)
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)
        ToolRegistry.clear()
        ToolRegistry.register(EchoTool())

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()
        self._temp_dir.cleanup()

    async def test_tool_calls_are_recorded(self) -> None:
        tool_executor = ToolExecutor(
            recorder=ConversationToolRecorder(self.conversation_repo, max_payload_bytes=64 * 1024)
        )
        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            work_path=None,
            metadata={},
            tool_name="echo_record",
            arguments={"text": "hello"},
            tool_call_id="call-1",
        )
        self.assertTrue(result.ok)

        events = await self.conversation_repo.list_latest(self.session_id, limit=10)
        kinds = [event.kind for event in events]
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)

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
