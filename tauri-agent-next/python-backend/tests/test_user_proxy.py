from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Optional

from agents.base import AgentBase
from agents.center import AgentCenter
from agents.instance import AgentInstance
from agents.message import AgentMessage
from agents.user_proxy import UserProxyAgent
from models import CreateRunRequest, StopRunResponse
from observation.observer import InMemoryExecutionObserver


@dataclass(slots=True)
class FakeActiveRun:
    run_id: str
    session_id: str
    entry_assistant_id: str
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
        entry_assistant_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FakeActiveRun:
        self._seq += 1
        run_id = f"run-{self._seq}"
        active_run = FakeActiveRun(
            run_id=run_id,
            session_id=session_id,
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
                role="user",
            ),
            self.center,
            observer=self.observer,
            run_manager=self.run_manager,
            roster_manager=self.roster_manager,
        )
        await self.center.register(self.user)
        await self.center.register(self.assistant)

    async def asyncTearDown(self) -> None:
        self.assistant.response_gate.set()
        await self.center.drain()

    async def test_start_run_waits_then_finishes_on_response(self) -> None:
        accepted = await self.user._start_run(
            CreateRunRequest(content="hello", session_id="session-1")
        )
        await asyncio.wait_for(self.assistant.request_received.wait(), timeout=1.0)

        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(self.user.instance.status, "waiting")
        self.assertEqual(self.run_manager.finish_calls, [])

        self.assistant.response_gate.set()
        await self.center.drain()

        self.assertEqual(len(self.run_manager.finish_calls), 1)
        self.assertEqual(self.run_manager.finish_calls[0]["run_id"], accepted.run_id)
        self.assertEqual(self.run_manager.finish_calls[0]["result_payload"]["reply"], "done")
        self.assertEqual(self.user.instance.status, "idle")

    async def test_failed_response_fails_run_and_returns_idle(self) -> None:
        self.assistant.response_ok = False
        self.assistant.response_payload = {"error": "boom", "status": "error"}

        accepted = await self.user._start_run(
            CreateRunRequest(content="hello", session_id="session-1")
        )
        await asyncio.wait_for(self.assistant.request_received.wait(), timeout=1.0)

        self.assertEqual(self.user.instance.status, "waiting")

        self.assistant.response_gate.set()
        await self.center.drain()

        self.assertEqual(len(self.run_manager.fail_calls), 1)
        self.assertEqual(self.run_manager.fail_calls[0]["run_id"], accepted.run_id)
        self.assertEqual(self.run_manager.fail_calls[0]["error_text"], "boom")
        self.assertEqual(self.user.instance.status, "idle")

    async def test_stop_clears_pending_and_ignores_late_response(self) -> None:
        accepted = await self.user._start_run(
            CreateRunRequest(content="hello", session_id="session-1")
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
        self.assertEqual(self.user.instance.status, "idle")

        self.assistant.response_gate.set()
        await self.center.drain()

        self.assertEqual(self.run_manager.finish_calls, [])
        self.assertEqual(self.run_manager.fail_calls, [])
        ignored = [
            event
            for event in self.observer.list_events(agent_id=self.user.agent_id)
            if event.event_type == "user_proxy.rpc_response_ignored"
        ]
        self.assertTrue(ignored)
