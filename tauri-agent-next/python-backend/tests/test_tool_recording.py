from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
