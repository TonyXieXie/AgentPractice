from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from agents.base import AgentBase
from agents.center import AgentCenter
from agents.assistant import AssistantAgent
from agents.instance import AgentInstance
from agents.message import AgentMessage
from agents.user_proxy import UserProxyAgent
from models import CreateRunRequest, StopRunResponse
from observation.observer import InMemoryExecutionObserver
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository
from agents.execution import TaskManager


@dataclass(slots=True)
class FakeActiveRun:
    run_id: str
    session_id: str
    controller_agent_id: str
    entry_assistant_id: Optional[str]
    metadata: Dict[str, Any]


class RecordingRunManager:
    def __init__(self) -> None:
        self._active_runs: Dict[str, FakeActiveRun] = {}
        self._busy_sessions: Dict[str, str] = {}
        self._seq = 0
        self.finish_calls: list[Dict[str, Any]] = []
        self.fail_calls: list[Dict[str, Any]] = []
        self.stop_calls: list[str] = []

    async def get_active_run_by_session(self, session_id: str) -> Optional[FakeActiveRun]:
        run_id = self._busy_sessions.get(session_id)
        if not run_id:
            return None
        return self._active_runs.get(run_id)

    async def open_run(
        self,
        session_id: str,
        controller_agent_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        entry_assistant_id: Optional[str] = None,
    ) -> FakeActiveRun:
        self._seq += 1
        run_id = f"run-{self._seq}"
        active_run = FakeActiveRun(
            run_id=run_id,
            session_id=session_id,
            controller_agent_id=controller_agent_id,
            entry_assistant_id=entry_assistant_id,
            metadata=dict(metadata or {}),
        )
        self._active_runs[run_id] = active_run
        self._busy_sessions[session_id] = run_id
        return active_run

    async def get_active_run(self, run_id: str) -> Optional[FakeActiveRun]:
        return self._active_runs.get(run_id)

    async def finish_run(
        self,
        run_id: str,
        result_payload: Dict[str, Any],
        message_id: Optional[str] = None,
    ) -> None:
        self.finish_calls.append(
            {
                "run_id": run_id,
                "result_payload": dict(result_payload),
                "message_id": message_id,
            }
        )
        await self._finalize_run(run_id)

    async def fail_run(
        self,
        run_id: str,
        error_text: str,
        *,
        message_id: Optional[str] = None,
        result_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.fail_calls.append(
            {
                "run_id": run_id,
                "error_text": error_text,
                "message_id": message_id,
                "result_payload": dict(result_payload or {}),
            }
        )
        await self._finalize_run(run_id)

    async def stop_run(self, run_id: str) -> Optional[StopRunResponse]:
        self.stop_calls.append(run_id)
        active_run = self._active_runs.get(run_id)
        if active_run is None:
            return None
        await self._finalize_run(run_id)
        return StopRunResponse(run_id=run_id, status="stopping")

    async def _finalize_run(self, run_id: str) -> None:
        active_run = self._active_runs.pop(run_id, None)
        if active_run is None:
            return
        if self._busy_sessions.get(active_run.session_id) == run_id:
            self._busy_sessions.pop(active_run.session_id, None)


class FakeRosterManager:
    def __init__(self, assistant_agent_id: str) -> None:
        self.assistant_agent_id = assistant_agent_id

    async def ensure_primary_entry_assistant(self, _session_id: str):
        return SimpleNamespace(id=self.assistant_agent_id)


class ControlledResponderAgent(AgentBase):
    def __init__(
        self,
        *args,
        response_payload: Optional[Dict[str, Any]] = None,
        response_ok: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.response_payload = dict(response_payload or {"reply": "done", "status": "completed"})
        self.response_ok = response_ok
        self.requests: list[AgentMessage] = []
        self.request_received = asyncio.Event()
        self.response_gate = asyncio.Event()

    async def on_message(self, message: AgentMessage):
        if message.message_type == "rpc" and message.rpc_phase == "request":
            self.requests.append(message)
            self.request_received.set()
            await self.response_gate.wait()
            return AgentMessage.build_rpc_response(
                request=message,
                sender_id=self.agent_id,
                payload=dict(self.response_payload),
                ok=self.response_ok,
                visibility="public" if self.response_ok else "internal",
                level="info" if self.response_ok else "error",
            )
        return None


class UserProxyAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        store = SqliteStore(Path(self._temp_dir.name) / "user_proxy_tests.db")
        await store.initialize()
        self.session_repository = SessionRepository(store)
        await self.session_repository.create(session_id="session-1")
        self.task_manager = TaskManager(TaskRepository(store))
        self.observer = InMemoryExecutionObserver()
        self.center = AgentCenter(observer=self.observer)
        self.run_manager = RecordingRunManager()
        self.center.run_manager = self.run_manager
        self.assistant = ControlledResponderAgent(
            AgentInstance(
                id="assistant-1",
                agent_type="assistant",
                role="assistant",
            ),
            self.center,
            observer=self.observer,
        )
        self.roster_manager = FakeRosterManager(self.assistant.agent_id)
        self.user = UserProxyAgent(
            AgentInstance(
                id="user-1",
                agent_type="user_proxy",
                role="user_proxy",
            ),
            self.center,
            observer=self.observer,
            run_manager=self.run_manager,
            roster_manager=self.roster_manager,
            task_manager=self.task_manager,
        )
        await self.center.register(self.user)
        await self.center.register(self.assistant)

    async def asyncTearDown(self) -> None:
        self.assistant.response_gate.set()
        await self.center.drain()
        self._temp_dir.cleanup()

    async def test_user_proxy_fails_run_when_rpc_response_has_no_follow_up_directive(self) -> None:
        accepted = await self.user._start_run(
            CreateRunRequest(
                content="hello",
                session_id="session-1",
                request_overrides={
                    "tool_name": "send_rpc_request",
                    "tool_arguments": {
                        "topic": "task.run",
                        "payload": {
                            "content": "hello worker",
                            "session_id": "session-1",
                        },
                        "target_agent_id": self.assistant.agent_id,
                    },
                },
            )
        )

        await asyncio.wait_for(self.assistant.request_received.wait(), timeout=1.0)
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(self.assistant.requests[0].topic, "task.run")
        self.assertEqual(self.assistant.requests[0].run_id, accepted.run_id)
        self.assertEqual(self.run_manager.finish_calls, [])

        self.assistant.response_gate.set()
        await self.center.drain()

        self.assertEqual(self.run_manager.finish_calls, [])
        self.assertEqual(len(self.run_manager.fail_calls), 1)
        self.assertEqual(self.run_manager.fail_calls[0]["run_id"], accepted.run_id)
        self.assertIsNone(await self.run_manager.get_active_run(accepted.run_id))
        protocol_errors = [
            event
            for event in self.observer.list_events(agent_id=self.user.agent_id)
            if event.event_type == "user_proxy.protocol_error"
        ]
        self.assertTrue(protocol_errors)

    async def test_user_proxy_finishes_run_only_with_explicit_finish_directive(self) -> None:
        self.assistant.response_payload = {
            "reply": "worker done",
            "status": "completed",
            "request_overrides": {
                "tool_name": "finish_run",
                "tool_arguments": {
                    "reply": "final answer",
                    "status": "completed",
                },
            },
        }

        accepted = await self.user._start_run(
            CreateRunRequest(
                content="hello",
                session_id="session-1",
                request_overrides={
                    "tool_name": "send_rpc_request",
                    "tool_arguments": {
                        "topic": "task.run",
                        "payload": {
                            "content": "hello worker",
                            "session_id": "session-1",
                        },
                        "target_agent_id": self.assistant.agent_id,
                    },
                },
            )
        )
        await asyncio.wait_for(self.assistant.request_received.wait(), timeout=1.0)

        self.assistant.response_gate.set()
        await self.center.drain()

        self.assertEqual(len(self.run_manager.finish_calls), 1)
        self.assertEqual(self.run_manager.finish_calls[0]["run_id"], accepted.run_id)
        self.assertEqual(self.run_manager.finish_calls[0]["result_payload"]["reply"], "final answer")
        self.assertIsNone(await self.run_manager.get_active_run(accepted.run_id))

    async def test_first_action_without_legal_directive_fails_run(self) -> None:
        accepted = await self.user._start_run(
            CreateRunRequest(
                content="hold",
                session_id="session-1",
            )
        )
        await self.center.drain()

        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(len(self.run_manager.fail_calls), 1)
        self.assertEqual(self.run_manager.fail_calls[0]["run_id"], accepted.run_id)
        self.assertEqual(self.user.instance.status, "error")
        self.assertIsNone(await self.run_manager.get_active_run(accepted.run_id))

    async def test_run_stop_remains_command_style(self) -> None:
        accepted = await self.user._start_run(
            CreateRunRequest(
                content="stop later",
                session_id="session-1",
                request_overrides={
                    "tool_name": "send_rpc_request",
                    "tool_arguments": {
                        "topic": "task.run",
                        "payload": {
                            "content": "hello worker",
                            "session_id": "session-1",
                        },
                        "target_agent_id": self.assistant.agent_id,
                    },
                },
            )
        )
        await asyncio.wait_for(self.assistant.request_received.wait(), timeout=1.0)

        stop_request = AgentMessage.build_rpc_request(
            topic="run.stop",
            sender_id="external:http",
            target_id=self.user.agent_id,
            payload={"run_id": accepted.run_id},
            run_id=accepted.run_id,
            session_id="session-1",
            visibility="internal",
        )
        correlation_id = stop_request.correlation_id or stop_request.id
        future = await self.center.expect_rpc_response(correlation_id)
        try:
            await self.center.route(stop_request)
            stop_response = await asyncio.wait_for(future, timeout=1.0)
        finally:
            await self.center.clear_rpc_waiter(correlation_id, future)

        self.assertTrue(stop_response.ok)
        self.assertEqual(stop_response.payload["status"], "stopping")
        self.assertEqual(self.run_manager.stop_calls, [accepted.run_id])
        self.assertEqual(self.user.instance.status, "idle")

    async def test_tool_visibility_restricts_terminal_directives_to_user_proxy(self) -> None:
        assistant = AssistantAgent(
            AgentInstance(id="assistant-runtime", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant_tools = {tool.name for tool in assistant.execution_engine.tool_executor.list_tools()}
        user_tools = {tool.name for tool in self.user.execution_engine.tool_executor.list_tools()}

        self.assertIn("send_rpc_request", assistant_tools)
        self.assertIn("send_rpc_response", assistant_tools)
        self.assertIn("send_event", assistant_tools)
        self.assertIn("broadcast_event", assistant_tools)
        self.assertNotIn("finish_run", assistant_tools)
        self.assertNotIn("fail_run", assistant_tools)
        self.assertNotIn("stop_run", assistant_tools)
        self.assertIn("finish_run", user_tools)
        self.assertIn("fail_run", user_tools)
        self.assertIn("stop_run", user_tools)
        self.assertNotIn("wait", assistant_tools)
        self.assertNotIn("wait", user_tools)
