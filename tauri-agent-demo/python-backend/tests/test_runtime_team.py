import asyncio
import copy
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database
import team_coordinator as team_coordinator_module
from agents.base import AgentStep
from fastapi import HTTPException
from models import ChatMessageCreate, ChatSessionCreate, ChatSessionUpdate, LLMConfigCreate
from repositories import chat_repository, config_repository, session_repository, team_repository
from server.services import session_service
from team_coordinator import TeamCoordinator
from tools.builtin.handoff_tool import HandoffTool
from tools.context import reset_tool_context, set_tool_context


def _build_app_config():
    return {
        "agent": {
            "default_profile": "planner",
            "profiles": [
                {"id": "planner", "name": "Planner", "abilities": []},
                {"id": "coder", "name": "Coder", "abilities": []},
                {"id": "tester", "name": "Tester", "abilities": []},
            ],
            "team": {
                "execution_mode": "multi_session",
                "default_agent": "planner",
                "members": [
                    {"profile_id": "planner", "handoff_to": ["coder", "tester"]},
                    {"profile_id": "coder", "handoff_to": ["planner", "tester"]},
                    {"profile_id": "tester", "handoff_to": ["planner", "coder"]},
                ],
            },
            "teams": [
                {
                    "id": "delivery",
                    "name": "Delivery Team",
                    "leader_profile_id": "planner",
                    "member_profile_ids": ["planner", "coder", "tester"],
                }
            ],
        }
    }


class RuntimeTeamTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_db = database.Database(os.path.join(self.tempdir.name, "runtime-team.sqlite3"))
        self._patched_attrs = []
        self._patch_attr(database, "db", self.temp_db)
        self._patch_attr(chat_repository, "db", self.temp_db)
        self._patch_attr(config_repository, "db", self.temp_db)
        self._patch_attr(session_repository, "db", self.temp_db)
        self._patch_attr(team_repository, "db", self.temp_db)
        self.app_config = _build_app_config()
        self._patch_attr(session_service, "get_app_config", lambda: copy.deepcopy(self.app_config))
        self._patch_attr(session_service, "schedule_ast_scan", lambda *_args, **_kwargs: None)

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

    def _make_coordinator(self, runner):
        coordinator = TeamCoordinator(copy.deepcopy(self.app_config))
        coordinator._run_role_session_turn = types.MethodType(runner, coordinator)
        return coordinator

    def test_create_session_api_strips_runtime_team_fields(self):
        created = session_service.create_session(
            ChatSessionCreate(
                title="User Session",
                config_id=self.config.id,
                agent_profile="planner",
                team_id="runtime-team",
                role_key="planner",
                parent_session_id="external-parent",
            )
        )

        self.assertIsNone(created.team_id)
        self.assertIsNone(created.role_key)
        self.assertIsNone(created.parent_session_id)

    def test_update_session_api_blocks_runtime_team_binding_changes(self):
        session = self._create_session(
            title="Planner",
            agent_team_id="delivery",
            team_id="runtime-team",
            role_key="planner",
        )

        with self.assertRaises(HTTPException) as agent_error:
            session_service.update_session(session.id, ChatSessionUpdate(agent_profile="coder"))
        self.assertEqual(agent_error.exception.status_code, 400)

        with self.assertRaises(HTTPException) as runtime_error:
            session_service.update_session(session.id, ChatSessionUpdate(team_id="other-runtime-team"))
        self.assertEqual(runtime_error.exception.status_code, 400)

    def test_handoff_tool_requires_work_summary_in_multi_session_mode(self):
        tool = HandoffTool().bind_context(
            {
                "app_config": self.app_config,
                "current_agent_profile": "planner",
                "current_agent_team_id": None,
            }
        )
        token = set_tool_context(
            {
                "app_config": self.app_config,
                "current_agent_profile": "planner",
                "current_agent_team_id": None,
            }
        )
        try:
            result = asyncio.run(tool.execute('{"target_agent":"coder","reason":"Implement the change"}'))
        finally:
            reset_tool_context(token)

        payload = json.loads(result)
        self.assertEqual(payload.get("status"), "error")
        self.assertIn("work_summary", str(payload.get("error")))

    def test_multi_session_handoff_reuses_role_session_and_mirrors_events(self):
        source = self._create_session(title="Planner Root", agent_profile="planner")

        async def fake_runner(
            self,
            session_id,
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
            handoff_id,
            parent_handoff_id=None,
        ):
            chat_repository.create_message(
                ChatMessageCreate(
                    session_id=session_id,
                    role="assistant",
                    content=f"{target_role} handled: {reason}",
                    metadata={"agent_profile": target_role},
                )
            )
            return {
                "status": "ok",
                "result": f"{target_role} handled: {reason}",
                "assistant_message_id": 1,
                "session_id": session_id,
                "agent_profile": target_role,
            }

        coordinator = self._make_coordinator(fake_runner)

        first = asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=source.id,
                from_agent="planner",
                to_agent="coder",
                reason="Implement the change",
                work_summary="Reviewed the request and prepared the implementation plan.",
                task_payload="User asked for the implementation.",
            )
        )
        self.assertEqual(first["status"], "ok")

        source_after = session_repository.get_session(source.id, include_count=False)
        self.assertIsNotNone(source_after.team_id)
        self.assertEqual(source_after.role_key, "planner")

        target = session_repository.get_session_by_runtime_team_role(source_after.team_id, "coder")
        self.assertIsNotNone(target)
        self.assertEqual(target.role_key, "coder")
        self.assertEqual(target.agent_profile, "coder")

        events = team_repository.list_handoff_events(source_after.team_id)
        self.assertEqual([event.event_kind for event in events], ["requested", "started", "completed"])
        self.assertTrue(all(event.work_summary == "Reviewed the request and prepared the implementation plan." for event in events))

        for session_id in (source_after.id, target.id):
            messages = session_repository.list_messages(session_id)
            handoff_messages = [
                message for message in messages
                if isinstance(message.metadata, dict) and message.metadata.get("event_type") == "handoff"
            ]
            self.assertEqual(len(handoff_messages), 1)
            message = handoff_messages[0]
            self.assertEqual(message.role, "assistant")
            self.assertEqual(message.metadata.get("team_id"), source_after.team_id)
            self.assertTrue(message.metadata.get("handoff_id"))
            self.assertTrue(message.metadata.get("team_handoff_event_id"))
            self.assertEqual(message.metadata.get("event_kind"), "completed")
            self.assertEqual(
                message.metadata.get("work_summary"),
                "Reviewed the request and prepared the implementation plan.",
            )
            self.assertTrue(str(message.content or "").startswith("[Planner]:"))
            self.assertIn("Requested from [Coder]: Implement the change", str(message.content or ""))

        target_messages = session_repository.list_messages(target.id)
        delegated_user_messages = [
            message for message in target_messages
            if message.role == "user" and isinstance(message.metadata, dict) and message.metadata.get("delegated_turn")
        ]
        self.assertEqual(len(delegated_user_messages), 1)
        self.assertEqual(delegated_user_messages[0].content, "User asked for the implementation.")
        self.assertEqual(
            delegated_user_messages[0].metadata.get("work_summary"),
            "Reviewed the request and prepared the implementation plan.",
        )

        source_messages = session_repository.list_messages(source_after.id)
        delegated_result_messages = [
            message for message in source_messages
            if message.role == "assistant"
            and isinstance(message.metadata, dict)
            and message.metadata.get("event_type") == "delegated_result"
        ]
        self.assertEqual(len(delegated_result_messages), 1)
        self.assertEqual(delegated_result_messages[0].metadata.get("from_role_key"), "planner")
        self.assertEqual(delegated_result_messages[0].metadata.get("to_role_key"), "coder")
        self.assertTrue(str(delegated_result_messages[0].content or "").startswith("[Coder]:"))
        self.assertIn("coder handled: Implement the change", delegated_result_messages[0].content)

        second = asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=source.id,
                from_agent="planner",
                to_agent="coder",
                reason="Implement the follow-up",
                work_summary="Prepared the follow-up task for implementation.",
                task_payload="User asked for the follow-up.",
            )
        )
        self.assertEqual(second["status"], "ok")

        reused_target = session_repository.get_session_by_runtime_team_role(source_after.team_id, "coder")
        self.assertEqual(reused_target.id, target.id)
        self.assertEqual(second["target_session_id"], target.id)
        self.assertEqual(len(session_repository.get_sessions_by_runtime_team(source_after.team_id)), 2)
        self.assertEqual(len(team_repository.list_handoff_events(source_after.team_id)), 6)
        second_handoff_messages = [
            message for message in session_repository.list_messages(source_after.id)
            if isinstance(message.metadata, dict) and message.metadata.get("event_type") == "handoff"
        ]
        self.assertEqual(len(second_handoff_messages), 2)

    def test_nested_handoff_links_parent_chain(self):
        source = self._create_session(title="Planner Root", agent_profile="planner")

        async def nested_runner(
            self,
            session_id,
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
            handoff_id,
            parent_handoff_id=None,
        ):
            if target_role == "coder":
                nested = await self.execute_delegated_turn(
                    source_session_id=session_id,
                    from_agent="coder",
                    to_agent="tester",
                    reason="Validate the delegated work",
                    work_summary="Coder implemented the requested changes and needs validation.",
                    task_payload=task_payload,
                    parent_handoff_id=handoff_id,
                )
                return {
                    "status": nested["status"],
                    "result": nested.get("result") or nested.get("error") or "",
                    "assistant_message_id": 1,
                    "session_id": session_id,
                    "agent_profile": target_role,
                }
            return {
                "status": "ok",
                "result": "tester verified the work",
                "assistant_message_id": 1,
                "session_id": session_id,
                "agent_profile": target_role,
            }

        coordinator = self._make_coordinator(nested_runner)
        result = asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=source.id,
                from_agent="planner",
                to_agent="coder",
                reason="Implement and validate",
                work_summary="Planned the work and delegated implementation.",
                task_payload="User asked for an end-to-end implementation.",
            )
        )

        self.assertEqual(result["status"], "ok")
        team_id = session_repository.get_session(source.id, include_count=False).team_id
        events = team_repository.list_handoff_events(team_id)
        self.assertEqual(len(events), 6)
        outer_handoff_id = events[0].handoff_id
        child_events = [event for event in events if event.parent_handoff_id == outer_handoff_id]
        self.assertEqual(len(child_events), 3)
        self.assertEqual({event.event_kind for event in child_events}, {"requested", "started", "completed"})
        self.assertTrue(all(event.from_role_key == "coder" for event in child_events))
        self.assertTrue(all(event.to_role_key == "tester" for event in child_events))

    def test_non_leader_handoff_to_leader_skips_recursive_leader_turn(self):
        source = self._create_session(
            title="Planner Root",
            agent_profile="planner",
            agent_team_id="delivery",
        )
        coordinator = TeamCoordinator(copy.deepcopy(self.app_config))
        runtime_team, source = coordinator.ensure_team(source.id, "planner")
        coder_session, _ = asyncio.run(coordinator.resolve_or_create_role_session(runtime_team.id, "coder", source))

        runner_calls = {"count": 0}

        async def unexpected_runner(
            self,
            session_id,
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
            handoff_id,
            parent_handoff_id=None,
        ):
            runner_calls["count"] += 1
            return {
                "status": "ok",
                "result": "leader turn should not run recursively",
                "assistant_message_id": 1,
                "session_id": session_id,
                "agent_profile": target_role,
            }

        coordinator._run_role_session_turn = types.MethodType(unexpected_runner, coordinator)

        result = asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=coder_session.id,
                from_agent="coder",
                to_agent="planner",
                reason="Need leader decision",
                work_summary="Implemented a draft and need leader approval.",
                task_payload="User asked for an implementation.",
            )
        )

        self.assertEqual(result["status"], "returned_to_leader")
        self.assertEqual(result["from_role_key"], "coder")
        self.assertEqual(result["to_role_key"], "planner")
        self.assertEqual(runner_calls["count"], 0)
        self.assertIn("Returned control to [Planner]", result["return_summary"])

        team_events = team_repository.list_handoff_events(runtime_team.id)
        self.assertEqual([event.event_kind for event in team_events[-3:]], ["requested", "started", "completed"])
        self.assertEqual(team_events[-1].from_role_key, "coder")
        self.assertEqual(team_events[-1].to_role_key, "planner")

    def test_leader_receives_return_to_leader_report_instead_of_coder_completion(self):
        source = self._create_session(
            title="Planner Root",
            agent_profile="planner",
            agent_team_id="delivery",
        )

        async def fake_runner(
            self,
            session_id,
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
            handoff_id,
            parent_handoff_id=None,
        ):
            return {
                "status": "returned_to_leader",
                "source_session_id": session_id,
                "target_session_id": source.id,
                "from_role_key": "coder",
                "to_role_key": "planner",
                "reason": "Need leader approval before finalizing.",
                "work_summary": "Prepared a working draft but need planner confirmation.",
                "return_summary": (
                    "[Coder] Returned control to [Planner] for leader decision.\n"
                    "Work summary: Prepared a working draft but need planner confirmation.\n"
                    "Why leader is needed: Need leader approval before finalizing.\n"
                    "Only the leader may decide whether the overall user task is complete."
                ),
            }

        coordinator = self._make_coordinator(fake_runner)
        result = asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=source.id,
                from_agent="planner",
                to_agent="coder",
                reason="Implement the change",
                work_summary="Planner delegated the implementation.",
                task_payload="User asked for an implementation.",
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["from_role_key"], "planner")
        self.assertEqual(result["to_role_key"], "coder")
        self.assertIn("Returned control to [Planner]", result["result"])

        delegated_result_messages = [
            message
            for message in session_repository.list_messages(source.id)
            if message.role == "assistant"
            and isinstance(message.metadata, dict)
            and message.metadata.get("event_type") == "delegated_result"
        ]
        self.assertEqual(len(delegated_result_messages), 1)
        self.assertEqual(delegated_result_messages[0].metadata.get("from_role_key"), "planner")
        self.assertEqual(delegated_result_messages[0].metadata.get("to_role_key"), "coder")
        self.assertIn("Returned control to [Planner]", delegated_result_messages[0].content)
        self.assertNotIn("coder handled:", delegated_result_messages[0].content)

    def test_role_session_turn_emits_live_session_message_snapshots(self):
        target = self._create_session(
            title="Coder Role",
            agent_profile="coder",
            agent_team_id="delivery",
            team_id="runtime-team-1",
            role_key="coder",
        )

        class DummyHub:
            def __init__(self):
                self.events = []

            async def emit(self, session_id, payload):
                self.events.append((session_id, payload))

        class FakeExecutor:
            async def run(self, user_input, history, session_id, request_overrides):
                yield AgentStep(step_type="observation", content="Inspecting repository files", metadata={})
                yield AgentStep(
                    step_type="answer",
                    content=(
                        "Completed work\n"
                        "- Implemented the requested change\n"
                        "- No blocking issues remain\n"
                        "- Recommended next step: planner review"
                    ),
                    metadata={},
                )

        hub = DummyHub()
        coordinator = TeamCoordinator(copy.deepcopy(self.app_config))
        self._patch_attr(team_coordinator_module, "get_ws_hub", lambda: hub)
        self._patch_attr(team_coordinator_module, "create_llm_client", lambda config: type("DummyClient", (), {"config": config})())
        self._patch_attr(
            team_coordinator_module,
            "maybe_compress_context",
            _async_return(
                lambda **kwargs: (
                    kwargs.get("current_summary", ""),
                    kwargs.get("last_compressed_call_id"),
                    kwargs.get("last_message_id"),
                    False,
                )
            ),
        )
        self._patch_attr(
            team_coordinator_module,
            "maybe_compress_private_context",
            _async_return(
                lambda **kwargs: (
                    kwargs.get("current_summary", ""),
                    kwargs.get("last_compressed_step_id"),
                    False,
                )
            ),
        )
        self._patch_attr(team_coordinator_module, "build_history_for_llm", lambda *args, **kwargs: [])
        self._patch_attr(
            team_coordinator_module,
            "build_agent_prompt_and_tools",
            lambda profile_id, _tools, include_tools=True, extra_context=None, exclude_ability_ids=None: (
                f"Prompt for {profile_id}",
                [],
                profile_id,
                [],
            ),
        )
        self._patch_attr(team_coordinator_module, "build_live_pty_prompt", lambda _session_id: "")
        self._patch_attr(team_coordinator_module, "append_reasoning_summary_prompt", lambda prompt, _summary: prompt)
        self._patch_attr(team_coordinator_module, "create_agent_executor", lambda *args, **kwargs: FakeExecutor())

        result = asyncio.run(
            coordinator._run_role_session_turn(
                session_id=target.id,
                source_role="planner",
                target_role="coder",
                leader_role="planner",
                reason="Implement the change",
                work_summary="Planner reviewed the task and defined the coding work.",
                task_payload="User asked for the implementation.",
                handoff_id="handoff-live-1",
            )
        )

        self.assertEqual(result["status"], "ok")
        session_events = [payload for session_id, payload in hub.events if session_id == target.id]
        self.assertGreaterEqual(len(session_events), 4)
        self.assertTrue(all(payload.get("type") == "session_message" for payload in session_events))

        delegated_user_event = session_events[0]
        self.assertEqual(delegated_user_event.get("active_agent_profile"), "coder")
        self.assertEqual(delegated_user_event["message"]["role"], "user")
        self.assertTrue(delegated_user_event["message"]["metadata"].get("delegated_turn"))

        assistant_start_event = session_events[1]
        assistant_live_event = session_events[2]
        assistant_final_event = session_events[-1]
        assistant_message_id = assistant_start_event["message"]["id"]

        self.assertEqual(assistant_start_event["message"]["role"], "assistant")
        self.assertEqual(assistant_start_event["message"]["content"], "")
        self.assertTrue(assistant_start_event["message"]["metadata"].get("agent_streaming"))
        self.assertEqual(assistant_start_event["message"]["metadata"].get("agent_steps"), [])

        self.assertEqual(assistant_live_event["message"]["id"], assistant_message_id)
        self.assertTrue(assistant_live_event["message"]["metadata"].get("agent_streaming"))
        self.assertEqual(len(assistant_live_event["message"]["metadata"].get("agent_steps") or []), 1)
        self.assertEqual(
            assistant_live_event["message"]["metadata"]["agent_steps"][0]["step_type"],
            "observation",
        )

        self.assertEqual(assistant_final_event["message"]["id"], assistant_message_id)
        self.assertFalse(assistant_final_event["message"]["metadata"].get("agent_streaming"))
        self.assertIn("Completed work", assistant_final_event["message"]["content"])

    def test_role_session_turn_stops_local_execution_after_handoff(self):
        target = self._create_session(
            title="Coder Role",
            agent_profile="coder",
            agent_team_id="delivery",
            team_id="runtime-team-1",
            role_key="coder",
        )

        class DummyHub:
            def __init__(self):
                self.events = []

            async def emit(self, session_id, payload):
                self.events.append((session_id, payload))

        class FakeExecutor:
            def __init__(self):
                self.continued_after_handoff = False

            async def run(self, user_input, history, session_id, request_overrides):
                yield AgentStep(
                    step_type="observation",
                    content="Coder needs leader review before continuing",
                    metadata={
                        "handoff_requested": True,
                        "from_agent": "coder",
                        "target_agent": "planner",
                        "reason": "Need leader clarification",
                        "work_summary": "Prepared a draft implementation and need planner confirmation.",
                    },
                )
                self.continued_after_handoff = True
                yield AgentStep(
                    step_type="answer",
                    content="Coder continued after handoff unexpectedly",
                    metadata={},
                )

        hub = DummyHub()
        fake_executor = FakeExecutor()
        coordinator = TeamCoordinator(copy.deepcopy(self.app_config))
        self._patch_attr(team_coordinator_module, "get_ws_hub", lambda: hub)
        self._patch_attr(team_coordinator_module, "create_llm_client", lambda config: type("DummyClient", (), {"config": config})())
        self._patch_attr(
            team_coordinator_module,
            "maybe_compress_context",
            _async_return(
                lambda **kwargs: (
                    kwargs.get("current_summary", ""),
                    kwargs.get("last_compressed_call_id"),
                    kwargs.get("last_message_id"),
                    False,
                )
            ),
        )
        self._patch_attr(
            team_coordinator_module,
            "maybe_compress_private_context",
            _async_return(
                lambda **kwargs: (
                    kwargs.get("current_summary", ""),
                    kwargs.get("last_compressed_step_id"),
                    False,
                )
            ),
        )
        self._patch_attr(team_coordinator_module, "build_history_for_llm", lambda *args, **kwargs: [])
        self._patch_attr(
            team_coordinator_module,
            "build_agent_prompt_and_tools",
            lambda profile_id, _tools, include_tools=True, extra_context=None, exclude_ability_ids=None: (
                f"Prompt for {profile_id}",
                [],
                profile_id,
                [],
            ),
        )
        self._patch_attr(team_coordinator_module, "build_live_pty_prompt", lambda _session_id: "")
        self._patch_attr(team_coordinator_module, "append_reasoning_summary_prompt", lambda prompt, _summary: prompt)
        self._patch_attr(team_coordinator_module, "create_agent_executor", lambda *args, **kwargs: fake_executor)
        test_case = self

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
            target_events = [payload for session_id, payload in hub.events if session_id == target.id]
            test_case.assertGreaterEqual(len(target_events), 3)
            last_target_event = target_events[-1]
            test_case.assertEqual(last_target_event["message"]["role"], "assistant")
            test_case.assertFalse(last_target_event["message"]["metadata"].get("agent_streaming"))
            test_case.assertEqual(last_target_event["message"]["content"], "")
            return {
                "status": "ok",
                "result": "Planner provided the final decision",
                "handoff_id": "handoff-transfer-1",
                "source_session_id": source_session_id,
                "target_session_id": "planner-session",
                "from_role_key": from_agent,
                "to_role_key": to_agent,
            }

        coordinator.execute_delegated_turn = types.MethodType(fake_execute_delegated_turn, coordinator)

        result = asyncio.run(
            coordinator._run_role_session_turn(
                session_id=target.id,
                source_role="planner",
                target_role="coder",
                leader_role="planner",
                reason="Implement the change",
                work_summary="Planner delegated the implementation work.",
                task_payload="User asked for the implementation.",
                handoff_id="handoff-root-1",
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], "Planner provided the final decision")
        self.assertFalse(fake_executor.continued_after_handoff)
        assistant_steps = chat_repository.list_agent_steps(target.id)
        self.assertEqual(len([step for step in assistant_steps if step.get("step_type") == "answer"]), 0)
        persisted_assistant = [
            message for message in session_repository.list_messages(target.id)
            if message.role == "assistant"
        ]
        self.assertEqual(len(persisted_assistant), 1)
        self.assertEqual(persisted_assistant[0].content, "")

    def test_copy_session_detaches_runtime_team_fields(self):
        source = self._create_session(
            title="Planner Root",
            agent_profile="planner",
            agent_team_id="delivery",
            team_id="runtime-team",
            role_key="planner",
            parent_session_id="upstream",
        )

        copied = session_repository.copy_session(source.id, "Planner Root Copy")
        self.assertIsNotNone(copied)
        self.assertEqual(copied.agent_team_id, "delivery")
        self.assertIsNone(copied.team_id)
        self.assertIsNone(copied.role_key)
        self.assertIsNone(copied.parent_session_id)

    def test_delete_root_session_cleans_runtime_team_records(self):
        source = self._create_session(title="Planner Root", agent_profile="planner")

        async def fake_runner(
            self,
            session_id,
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
            handoff_id,
            parent_handoff_id=None,
        ):
            return {
                "status": "ok",
                "result": "coder completed the work",
                "assistant_message_id": 1,
                "session_id": session_id,
                "agent_profile": target_role,
            }

        coordinator = self._make_coordinator(fake_runner)
        asyncio.run(
            coordinator.execute_delegated_turn(
                source_session_id=source.id,
                from_agent="planner",
                to_agent="coder",
                reason="Implement the change",
                work_summary="Prepared the initial implementation handoff.",
                task_payload="User asked for the implementation.",
            )
        )

        source_after = session_repository.get_session(source.id, include_count=False)
        target = session_repository.get_session_by_runtime_team_role(source_after.team_id, "coder")
        self.assertTrue(session_repository.delete_session(source.id))
        self.assertIsNone(session_repository.get_session(source.id, include_count=False))
        self.assertIsNone(session_repository.get_session(target.id, include_count=False))
        self.assertIsNone(team_repository.get_team(source_after.team_id))
        self.assertEqual(team_repository.list_handoff_events(source_after.team_id), [])

def _async_return(factory):
    async def _wrapper(*args, **kwargs):
        return factory(*args, **kwargs)

    return _wrapper


if __name__ == "__main__":
    unittest.main()
