import asyncio
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database
import server.chat_agent_runtime as chat_agent_runtime
from agents.base import AgentStep
from models import ChatMessageCreate, ChatRequest, ChatSessionCreate, LLMConfigCreate
from repositories import chat_repository, config_repository, session_repository, team_repository


def _build_app_config():
    return {
        "agent": {
            "default_profile": "planner",
            "profiles": [
                {"id": "planner", "name": "Planner", "abilities": []},
                {"id": "coder", "name": "Coder", "abilities": []},
            ],
            "team": {
                "execution_mode": "multi_session",
                "default_agent": "planner",
                "members": [
                    {"profile_id": "planner", "handoff_to": ["coder"]},
                    {"profile_id": "coder", "handoff_to": ["planner"]},
                ],
            },
            "teams": [
                {
                    "id": "delivery",
                    "name": "Delivery",
                    "leader_profile_id": "planner",
                    "member_profile_ids": ["planner", "coder"],
                }
            ],
        }
    }


class _DummyState:
    def __init__(self):
        self.stream_id = "test-stream"
        self.events = []
        self.init_payload = None
        self.done = False

    async def emit(self, payload):
        self.events.append(payload)

    async def set_init_payload(self, payload):
        self.init_payload = payload

    async def mark_done(self):
        self.done = True


class MultiAgentRuntimeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_db = database.Database(os.path.join(self.tempdir.name, "runtime-semantics.sqlite3"))
        self._patched_attrs = []
        for module in (database, chat_repository, config_repository, session_repository, team_repository):
            self._patch_attr(module, "db", self.temp_db)

        self.app_config = _build_app_config()
        self._patch_attr(chat_agent_runtime, "get_app_config", lambda: copy.deepcopy(self.app_config))
        self._patch_attr(chat_agent_runtime, "schedule_ast_scan", lambda *_args, **_kwargs: None)
        self._patch_attr(chat_agent_runtime, "create_llm_client", lambda config: type("DummyClient", (), {"config": config})())
        self._patch_attr(
            chat_agent_runtime,
            "maybe_compress_context",
            _async_return(lambda **kwargs: (
                kwargs.get("current_summary", ""),
                kwargs.get("last_compressed_call_id"),
                None,
                False,
            )),
        )
        self._patch_attr(
            chat_agent_runtime,
            "maybe_compress_private_context",
            _async_return(lambda **kwargs: (
                kwargs.get("current_summary", ""),
                kwargs.get("last_compressed_step_id"),
                False,
            )),
        )
        self._patch_attr(
            chat_agent_runtime,
            "build_agent_prompt_and_tools",
            lambda profile_id, _tools, include_tools=True, extra_context=None, exclude_ability_ids=None: (
                f"Prompt for {profile_id}",
                [],
                profile_id,
                [],
            ),
        )
        self._patch_attr(chat_agent_runtime, "build_live_pty_prompt", lambda _session_id: "")
        self._patch_attr(chat_agent_runtime, "append_reasoning_summary_prompt", lambda prompt, _summary: prompt)
        self._patch_attr(chat_agent_runtime, "maybe_update_session_title", _async_return(lambda **_kwargs: None))
        self._patch_attr(chat_agent_runtime, "get_enabled_skills", lambda: [])
        self._patch_attr(chat_agent_runtime, "extract_skill_invocations", lambda *_args, **_kwargs: [])
        self._patch_attr(chat_agent_runtime, "build_skill_prompt_sections", lambda *_args, **_kwargs: "")

        self.config = config_repository.create_config(
            LLMConfigCreate(
                name="test-config",
                api_profile="openai",
                api_key="test-key",
                model="gpt-test",
            )
        )

    def tearDown(self):
        while self._patched_attrs:
            module, attr_name, original_value = self._patched_attrs.pop()
            setattr(module, attr_name, original_value)
        self.tempdir.cleanup()

    def _patch_attr(self, module, attr_name, value):
        self._patched_attrs.append((module, attr_name, getattr(module, attr_name)))
        setattr(module, attr_name, value)

    def _create_session(self, title="Root", agent_profile="planner", **extra):
        payload = {
            "title": title,
            "config_id": self.config.id,
            "agent_profile": agent_profile,
        }
        payload.update(extra)
        return session_repository.create_session(ChatSessionCreate(**payload))

    def test_non_leader_team_session_cannot_accept_new_user_task(self):
        session = self._create_session(
            title="Coder Role",
            agent_profile="coder",
            agent_team_id="delivery",
            team_id="runtime-team-1",
            role_key="coder",
        )
        state = _DummyState()

        asyncio.run(chat_agent_runtime.run_agent_stream(ChatRequest(message="Do the work", session_id=session.id), state))

        messages = session_repository.list_messages(session.id)
        self.assertEqual(messages, [])
        error_events = [event for event in state.events if event.get("step_type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("leader session (planner)", error_events[0]["content"])
        self.assertTrue(state.done)

    def test_leader_continues_after_delegated_result_in_multi_session_mode(self):
        session = self._create_session(
            title="Planner Root",
            agent_profile="planner",
            agent_team_id="delivery",
        )
        state = _DummyState()
        executor_calls = {"count": 0}
        history_snapshots = []

        class FakeExecutor:
            def __init__(self, call_index):
                self.call_index = call_index

            async def run(self, user_input, history, session_id, request_overrides):
                history_snapshots.append(list(history))
                if self.call_index == 0:
                    yield AgentStep(
                        step_type="observation",
                        content="handoff requested",
                        metadata={
                            "handoff_requested": True,
                            "from_agent": "planner",
                            "target_agent": "coder",
                            "reason": "Implement the change",
                            "work_summary": "Reviewed the task and planned the implementation.",
                        },
                    )
                    return

                yield AgentStep(step_type="answer", content="Leader final answer", metadata={})

        def fake_create_agent_executor(*args, **kwargs):
            call_index = executor_calls["count"]
            executor_calls["count"] += 1
            return FakeExecutor(call_index)

        async def fake_execute_delegated_turn(
            self,
            source_session_id,
            from_agent,
            to_agent,
            reason,
            work_summary,
            task_payload,
            parent_handoff_id=None,
            inline_session_message_callback=None,
            inline_session_ids=None,
        ):
            chat_repository.create_message(
                ChatMessageCreate(
                    session_id=source_session_id,
                    role="assistant",
                    content="[Planner]: Reviewed the task and planned the implementation.\nAssigned to [Coder]: Implement the change",
                    metadata={
                        "event_type": "handoff",
                        "team_id": "runtime-team-1",
                        "handoff_id": "handoff-1",
                        "event_kind": "completed",
                        "from_agent": from_agent,
                        "to_agent": to_agent,
                    },
                )
            )
            delegated_message = chat_repository.create_message(
                ChatMessageCreate(
                    session_id=source_session_id,
                    role="assistant",
                    content="[Coder]: Completed work report",
                    metadata={
                        "event_type": "delegated_result",
                        "team_id": "runtime-team-1",
                        "handoff_id": "handoff-1",
                        "from_role_key": from_agent,
                        "to_role_key": to_agent,
                        "source_session_id": source_session_id,
                        "target_session_id": "target-session-1",
                    },
                )
            )
            if inline_session_message_callback and (not inline_session_ids or source_session_id in inline_session_ids):
                await inline_session_message_callback(
                    {
                        "type": "session_message",
                        "session_id": source_session_id,
                        "message": delegated_message.model_dump(),
                    }
                )
            return {
                "status": "ok",
                "result": "Completed work report",
                "handoff_id": "handoff-1",
                "source_session_id": source_session_id,
                "target_session_id": "target-session-1",
                "from_role_key": from_agent,
                "to_role_key": to_agent,
            }

        self._patch_attr(chat_agent_runtime, "create_agent_executor", fake_create_agent_executor)
        self._patch_attr(chat_agent_runtime.TeamCoordinator, "execute_delegated_turn", fake_execute_delegated_turn)

        asyncio.run(chat_agent_runtime.run_agent_stream(ChatRequest(message="Ship this task", session_id=session.id), state))

        self.assertEqual(executor_calls["count"], 2)
        messages = session_repository.list_messages(session.id)
        delegated_results = [
            message
            for message in messages
            if message.role == "assistant"
            and isinstance(message.metadata, dict)
            and message.metadata.get("event_type") == "delegated_result"
        ]
        self.assertEqual(len(delegated_results), 1)
        assistant_messages = [
            message
            for message in messages
            if message.role == "assistant"
            and not (
                isinstance(message.metadata, dict)
                and message.metadata.get("event_type") in {"handoff", "delegated_result"}
            )
        ]
        self.assertEqual(len(assistant_messages), 2)
        self.assertEqual(assistant_messages[-1].content, "Leader final answer")
        self.assertFalse(assistant_messages[0].metadata.get("agent_streaming"))
        self.assertTrue(isinstance(assistant_messages[0].metadata, dict))
        self.assertTrue(isinstance(assistant_messages[-1].metadata, dict))
        self.assertNotEqual(assistant_messages[0].id, assistant_messages[-1].id)
        self.assertGreaterEqual(len(history_snapshots), 2)
        self.assertTrue(
            any(
                msg.get("role") == "assistant" and "[Coder]: Completed work report" in str(msg.get("content") or "")
                for msg in history_snapshots[1]
            ),
            history_snapshots[1],
        )
        self.assertTrue(
            any(
                msg.get("role") == "assistant"
                and "[Planner]:" in str(msg.get("content") or "")
                for msg in history_snapshots[1]
            ),
            history_snapshots[1],
        )
        self.assertTrue(
            all(
                msg.get("_after_user")
                for msg in history_snapshots[1]
                if msg.get("role") == "assistant"
                and ("[Planner]:" in str(msg.get("content") or "") or "[Coder]:" in str(msg.get("content") or ""))
            ),
            history_snapshots[1],
        )
        session_message_indices = [
            index for index, event in enumerate(state.events)
            if event.get("type") == "session_message"
            and event.get("message", {}).get("metadata", {}).get("event_type") == "delegated_result"
        ]
        answer_indices = [
            index for index, event in enumerate(state.events)
            if event.get("step_type") == "answer" and event.get("content") == "Leader final answer"
        ]
        assistant_switch_indices = [
            index for index, event in enumerate(state.events)
            if event.get("session_id") == session.id and isinstance(event.get("assistant_message_id"), int)
        ]
        self.assertEqual(len(session_message_indices), 1)
        self.assertEqual(len(answer_indices), 1)
        self.assertGreaterEqual(len(assistant_switch_indices), 2)
        self.assertLess(session_message_indices[0], answer_indices[0])
        self.assertLess(assistant_switch_indices[-1], answer_indices[0])

    def test_leader_can_handoff_back_to_same_agent_after_delegated_result(self):
        session = self._create_session(
            title="Planner Root",
            agent_profile="planner",
            agent_team_id="delivery",
        )
        state = _DummyState()
        executor_calls = {"count": 0}
        delegated_calls = {"count": 0}
        post_user_snapshots = []

        class FakeExecutor:
            def __init__(self, call_index):
                self.call_index = call_index

            async def run(self, user_input, history, session_id, request_overrides):
                post_user_snapshots.append(list(request_overrides.get("_post_user_messages") or []))
                if self.call_index == 0:
                    yield AgentStep(
                        step_type="observation",
                        content="handoff requested",
                        metadata={
                            "handoff_requested": True,
                            "from_agent": "planner",
                            "target_agent": "coder",
                            "reason": "Implement the change",
                            "work_summary": "Planned the implementation work.",
                        },
                    )
                    return
                if self.call_index == 1:
                    yield AgentStep(
                        step_type="observation",
                        content="follow-up handoff requested",
                        metadata={
                            "handoff_requested": True,
                            "from_agent": "planner",
                            "target_agent": "coder",
                            "reason": "Apply the follow-up revision",
                            "work_summary": "Reviewed the first coder report and prepared a narrower follow-up task.",
                        },
                    )
                    return

                yield AgentStep(
                    step_type="answer",
                    content="Planner final answer",
                    metadata={},
                )

        def fake_create_agent_executor(*args, **kwargs):
            call_index = executor_calls["count"]
            executor_calls["count"] += 1
            return FakeExecutor(call_index)

        async def fake_execute_delegated_turn(
            self,
            source_session_id,
            from_agent,
            to_agent,
            reason,
            work_summary,
            task_payload,
            parent_handoff_id=None,
            inline_session_message_callback=None,
            inline_session_ids=None,
        ):
            delegated_calls["count"] += 1
            delegated_index = delegated_calls["count"]
            result_text = (
                "First coder report: implementation outline is ready, but a focused follow-up change is still needed."
                if delegated_index == 1
                else "Second coder report: follow-up change applied and ready for leader review."
            )
            delegated_message = chat_repository.create_message(
                ChatMessageCreate(
                    session_id=source_session_id,
                    role="assistant",
                    content=f"[Coder]: {result_text}",
                    metadata={
                        "event_type": "delegated_result",
                        "team_id": "runtime-team-1",
                        "handoff_id": f"handoff-repeat-{delegated_index}",
                        "from_role_key": from_agent,
                        "to_role_key": to_agent,
                        "source_session_id": source_session_id,
                        "target_session_id": f"target-session-{delegated_index}",
                    },
                )
            )
            if inline_session_message_callback and (not inline_session_ids or source_session_id in inline_session_ids):
                await inline_session_message_callback(
                    {
                        "type": "session_message",
                        "session_id": source_session_id,
                        "message": delegated_message.model_dump(),
                    }
                )
            return {
                "status": "ok",
                "result": result_text,
                "handoff_id": f"handoff-repeat-{delegated_index}",
                "source_session_id": source_session_id,
                "target_session_id": f"target-session-{delegated_index}",
                "from_role_key": from_agent,
                "to_role_key": to_agent,
            }

        self._patch_attr(chat_agent_runtime, "create_agent_executor", fake_create_agent_executor)
        self._patch_attr(chat_agent_runtime.TeamCoordinator, "execute_delegated_turn", fake_execute_delegated_turn)

        asyncio.run(chat_agent_runtime.run_agent_stream(ChatRequest(message="Ask coder to implement the task", session_id=session.id), state))

        self.assertEqual(delegated_calls["count"], 2)
        self.assertEqual(executor_calls["count"], 3)
        messages = session_repository.list_messages(session.id)
        assistant_messages = [
            message
            for message in messages
            if message.role == "assistant"
            and not (
                isinstance(message.metadata, dict)
                and message.metadata.get("event_type") in {"handoff", "delegated_result"}
            )
        ]
        self.assertEqual(len(assistant_messages), 3)
        self.assertEqual(assistant_messages[-1].content, "Planner final answer")
        delegated_results = [
            message
            for message in messages
            if message.role == "assistant"
            and isinstance(message.metadata, dict)
            and message.metadata.get("event_type") == "delegated_result"
        ]
        self.assertEqual(len(delegated_results), 2)
        validation_events = [
            event for event in state.events
            if event.get("step_type") == "observation"
            and event.get("metadata", {}).get("runtime_validation") == "repeat_handoff_blocked"
        ]
        self.assertEqual(len(validation_events), 0)
        self.assertTrue(all(not snapshot for snapshot in post_user_snapshots), post_user_snapshots)


def _async_return(factory):
    async def _wrapper(*args, **kwargs):
        return factory(*args, **kwargs)

    return _wrapper


if __name__ == "__main__":
    unittest.main()
