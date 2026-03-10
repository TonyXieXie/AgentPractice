from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.execution.agent_memory import PRIVATE_CONTEXT_SUMMARY_MARKER, AgentMemory
from agents.message import AgentMessage
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.conversation_repository import ConversationRepository
from repositories.message_center_repository import MessageCenterRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore


class FakeConfig:
    api_profile = "openai"
    model = "fake-model"
    max_context_tokens = 4096
    max_tokens = 256


class FakeLLMClient:
    def __init__(self) -> None:
        self.config = FakeConfig()

    async def chat(self, messages, request_overrides=None):
        return {"content": "SUMMARY"}


class AgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(self.runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.message_center_repo = MessageCenterRepository(self.store)
        self.conversation_repo = ConversationRepository(self.store)
        self.agent_state_repo = AgentPromptStateRepository(self.store)
        self.prompt_trace_repo = PromptTraceRepository(self.store)
        self.memory = AgentMemory(
            session_repository=self.session_repo,
            message_center_repository=self.message_center_repo,
            conversation_repository=self.conversation_repo,
            agent_prompt_state_repository=self.agent_state_repo,
            prompt_trace_repository=self.prompt_trace_repo,
        )
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_rollup_private_summary_advances_cursor_and_writes_trace(self) -> None:
        agent_id = "assistant-1"

        user_msg = AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id="user-1",
            target_id=agent_id,
            payload={"content": "hello"},
            run_id="run-1",
            session_id=self.session_id,
        )
        await self.message_center_repo.append_visible_message(
            session_id=self.session_id,
            viewer_agent_id=agent_id,
            message=user_msg,
        )

        for i in range(12):
            await self.conversation_repo.append_event(
                session_id=self.session_id,
                run_id="run-1",
                agent_id=agent_id,
                kind="tool_result",
                content={"output": f"m{i}", "error": ""},
                tool_name="echo",
                tool_call_id=f"call-{i}",
                ok=True,
            )

        request = type(
            "Req",
            (),
            {
                "session_id": self.session_id,
                "run_id": "run-1",
                "message_id": "msg-current",
                "agent_id": agent_id,
                "user_input": "continue",
                "history": [],
                "system_prompt": None,
            },
        )()

        llm_client = FakeLLMClient()
        messages = await self.memory.build_messages(
            request,
            llm_client=llm_client,
            default_system_prompt="base",
            max_history_events=50,
            budget_cfg={
                "context": {
                    "compression_enabled": True,
                    "keep_recent_events": 2,
                    "compress_start_pct": 75,
                    "compress_target_pct": 55,
                    "budget_safety_tokens": 0,
                    "truncation": {
                        "enabled": True,
                        "threshold_chars": 4000,
                        "head_chars": 800,
                        "tail_chars": 800,
                    },
                },
                "trace": {"enabled": True},
                "truncation": {
                    "enabled": True,
                    "threshold_chars": 4000,
                    "head_chars": 800,
                    "tail_chars": 800,
                },
            },
        )

        state = await self.agent_state_repo.get(self.session_id, agent_id)
        self.assertIsNotNone(state)
        self.assertGreater(state.summarized_until_event_id, 0)
        self.assertEqual(state.summary_text, "SUMMARY")

        self.assertTrue(
            any(
                msg.get("role") == "assistant"
                and str(msg.get("content") or "").startswith(PRIVATE_CONTEXT_SUMMARY_MARKER)
                for msg in messages
            )
        )
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "continue")

        trace = await self.prompt_trace_repo.get_latest(self.session_id, agent_id=agent_id)
        self.assertIsNotNone(trace)

