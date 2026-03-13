from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.execution.agent_memory import AgentMemory
from agents.execution.prompt_assembler import PromptAssembler
from agents.execution.prompt_ir import PromptIR
from agents.message import AgentMessage
from observation.center import ObservationCenter
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore


class FakeConfig:
    api_profile = "openai"
    api_format = "openai_chat_completions"
    model = "fake-model"
    max_context_tokens = 4096
    max_tokens = 256


class FakeLLMClient:
    def __init__(self) -> None:
        self.config = FakeConfig()
        self.calls = []

    async def chat(self, prompt_ir, request_overrides=None):
        self.calls.append(
            {
                "messages": list(prompt_ir.messages),
                "budget": dict(prompt_ir.budget),
                "trace": dict(prompt_ir.trace),
            }
        )
        return {"content": "我已经总结了之前的私有执行。"}


class AgentMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(self.runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.shared_repo = SharedFactRepository(self.store)
        self.private_repo = AgentPrivateEventRepository(self.store)
        self.agent_state_repo = AgentPromptStateRepository(self.store)
        self.prompt_trace_repo = PromptTraceRepository(self.store)
        self.observation_center = ObservationCenter(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
        )
        self.memory = AgentMemory(
            session_repository=self.session_repo,
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
            agent_prompt_state_repository=self.agent_state_repo,
            prompt_trace_repository=self.prompt_trace_repo,
            observation_center=self.observation_center,
        )
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_history_renders_shared_and_private_in_causal_order_without_legacy_markers(self) -> None:
        agent_id = "Planner"
        await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            sender_id="external:http",
            topic="run.submit",
            fact_type="rpc_request",
            payload_json={"content": "重新设计数据模型"},
        )
        handoff_fact = await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            run_id="run-1",
            message_id="msg-shared-1",
            sender_id="UserProxy",
            target_agent_id=agent_id,
            topic="task.plan",
            fact_type="event_handoff",
            payload_json={"content": "用户要求重新设计数据模型，交给 Planner 执行。"},
        )
        await self.observation_center.append_private_event(
            session_id=self.session_id,
            owner_agent_id=agent_id,
            run_id="run-1",
            message_id="msg-shared-1",
            trigger_fact_id=handoff_fact.fact_id,
            kind="tool_call",
            payload_json={"tool_name": "read_file", "arguments": {"path": "design.md"}},
        )

        current_message = AgentMessage.build_event(
            topic="task.plan",
            sender_id="UserProxy",
            target_id=agent_id,
            payload={"content": "用户要求重新设计数据模型，交给 Planner 执行。"},
            run_id="run-1",
            session_id=self.session_id,
        ).model_copy(update={"id": "msg-shared-1"})

        history = await self.memory.build_history_for_agent(
            current_message,
            agent_id=agent_id,
            llm_client=None,
            max_history_events=20,
        )

        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "重新设计数据模型")
        self.assertTrue(history[1]["content"].startswith("[Planner] "))
        joined = "\n".join(item["content"] for item in history)
        self.assertNotIn("交给 Planner 执行。", joined)
        self.assertNotIn("[RPC request]", joined)
        self.assertNotIn("[Tool result]", joined)
        self.assertNotIn("fact_id=", joined)

    async def test_private_summary_is_cached_but_sourced_from_private_events(self) -> None:
        agent_id = "Coder"
        trigger_fact = await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            run_id="run-1",
            sender_id="Planner",
            target_agent_id=agent_id,
            topic="task.code",
            fact_type="event_handoff",
            payload_json={"content": "规划完成，交给 Coder。"},
        )
        for idx in range(6):
            await self.observation_center.append_private_event(
                session_id=self.session_id,
                owner_agent_id=agent_id,
                run_id="run-1",
                message_id="msg-coder-1",
                trigger_fact_id=trigger_fact.fact_id,
                kind="tool_result",
                payload_json={
                    "tool_name": "write_file",
                    "ok": True,
                    "output": f"step-{idx}",
                },
            )

        current_message = AgentMessage.build_event(
            topic="task.code",
            sender_id="Planner",
            target_id=agent_id,
            payload={"content": "规划完成，交给 Coder。"},
            run_id="run-1",
            session_id=self.session_id,
        )
        llm_client = FakeLLMClient()
        history = await self.memory.build_history_for_agent(
            current_message,
            agent_id=agent_id,
            llm_client=llm_client,
            max_history_events=20,
            budget_cfg={
                "context": {
                    "compression_enabled": True,
                    "keep_recent_events": 2,
                    "compress_start_pct": 0,
                    "compress_target_pct": 0,
                    "budget_safety_tokens": 0,
                }
            },
        )

        state = await self.agent_state_repo.get(self.session_id, agent_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.summary_text, "我已经总结了之前的私有执行。")
        summary_events = await self.private_repo.list(
            self.session_id,
            owner_agent_id=agent_id,
            kind="private_summary",
            after_id=0,
            limit=10,
        )
        self.assertEqual(len(summary_events), 1)
        self.assertGreater(state.summarized_until_event_id, 0)
        self.assertTrue(any("我已经总结了之前的私有执行。" in item["content"] for item in history))

    async def test_prompt_assembler_builds_system_history_and_current_input(self) -> None:
        agent_id = "Planner"
        await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            sender_id="external:http",
            topic="run.submit",
            fact_type="rpc_request",
            payload_json={"content": "做一个方案"},
        )
        current_message = AgentMessage.build_event(
            topic="task.plan",
            sender_id="UserProxy",
            target_id=agent_id,
            payload={"content": "用户要求做一个方案。"},
            run_id="run-1",
            session_id=self.session_id,
        )
        assembler = PromptAssembler()
        history = await self.memory.build_history_for_agent(
            current_message,
            agent_id=agent_id,
            llm_client=None,
            max_history_events=20,
        )
        prompt_ir = PromptIR(
            messages=assembler.assemble(
                system_messages=assembler.build_system_messages(
                    current_message,
                    default_system_prompt="你是 Planner。",
                    tool_policy_text="按需使用工具。",
                ),
                history_messages=history,
                current_input=assembler.build_current_input(current_message),
            ),
            budget={},
            trace={"cfg": self.memory._resolve_cfg(None), "actions": []},
        )
        prompt_ir = await self.memory.ensure_budget_for_view(
            prompt_ir,
            llm_client=FakeLLMClient(),
            session_id=self.session_id,
            agent_id=agent_id,
            run_id="run-1",
            phase="build_view",
        )

        self.assertEqual(prompt_ir.messages[0]["role"], "system")
        self.assertEqual(prompt_ir.messages[1]["role"], "user")
        self.assertEqual(prompt_ir.messages[-1]["role"], "assistant")
        trace = await self.prompt_trace_repo.get_latest(self.session_id, agent_id=agent_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.actions.get("phase"), "build_view")
