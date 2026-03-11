from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from agents.execution.engine import ExecutionResult
from agents.execution.task_manager import TaskManager
from agents.message import AgentMessage
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository


class RecordingAgent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.status_updates: list[dict[str, object]] = []

    async def update_status(self, status, *, reason=None, metadata=None):
        self.status_updates.append(
            {
                "status": status,
                "reason": reason,
                "metadata": dict(metadata or {}),
            }
        )
        return None


class SuccessfulExecutionEngine:
    def __init__(self) -> None:
        self.messages = []

    async def execute(self, message):
        self.messages.append(message)
        return ExecutionResult(
            ok=True,
            payload={
                "reply": "done",
                "status": "completed",
                "strategy": "simple",
            },
        )


class FailingExecutionEngine:
    async def execute(self, message):
        raise RuntimeError("boom")


class BlockingExecutionEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.messages = []

    async def execute(self, message):
        self.messages.append(message)
        self.started.set()
        await asyncio.Event().wait()


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.task_repo = TaskRepository(self.store)
        self.task_manager = TaskManager(self.task_repo)
        self.session_id = "session-1"
        self.run_id = "run-1"
        await self.session_repo.create(session_id=self.session_id)
        self.agent = RecordingAgent("assistant-1")

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_host_message_task_persists_completed_task_and_injects_task_id(self) -> None:
        engine = SuccessfulExecutionEngine()
        message = self._build_message()

        result = await self.task_manager.host_message_task(
            message,
            agent=self.agent,
            execution_engine=engine,
        )

        self.assertTrue(result.ok)
        records = await self.task_repo.list_by_run(self.run_id)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.result["reply"], "done")
        self.assertEqual(engine.messages[0].metadata.get("task_id"), record.id)
        self.assertEqual(self.agent.status_updates[0]["status"], "running")
        self.assertEqual(self.agent.status_updates[-1]["status"], "idle")

    async def test_host_message_task_marks_failed_when_execution_raises(self) -> None:
        message = self._build_message()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await self.task_manager.host_message_task(
                message,
                agent=self.agent,
                execution_engine=FailingExecutionEngine(),
            )

        records = await self.task_repo.list_by_run(self.run_id)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_text, "boom")
        self.assertEqual(self.agent.status_updates[-1]["status"], "error")

    async def test_stop_hosted_tasks_marks_running_task_stopped_idempotently(self) -> None:
        engine = BlockingExecutionEngine()
        runner = asyncio.create_task(
            self.task_manager.host_message_task(
                self._build_message(),
                agent=self.agent,
                execution_engine=engine,
            )
        )

        await engine.started.wait()
        await self._wait_for_running_task()

        updated = await self.task_manager.stop_hosted_tasks(self.run_id)
        self.assertEqual(updated, 1)
        with self.assertRaises(asyncio.CancelledError):
            await runner

        records = await self.task_repo.list_by_run(self.run_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "stopped")
        self.assertEqual(self.agent.status_updates[-1]["status"], "idle")

        updated_again = await self.task_manager.stop_hosted_tasks(self.run_id)
        self.assertEqual(updated_again, 0)
        records = await self.task_repo.list_by_run(self.run_id)
        self.assertEqual(records[0].status, "stopped")

    async def test_host_message_task_accepts_rpc_response_sources(self) -> None:
        engine = SuccessfulExecutionEngine()
        request = self._build_message()
        response = AgentMessage.build_rpc_response(
            request=request,
            sender_id="assistant-2",
            payload={"reply": "done", "status": "completed"},
            ok=True,
        )

        result = await self.task_manager.host_message_task(
            response,
            agent=self.agent,
            execution_engine=engine,
        )

        self.assertTrue(result.ok)
        records = await self.task_repo.list_by_run(self.run_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_message_kind, "rpc_response")
        self.assertEqual(records[0].topic, request.topic)

    async def _wait_for_running_task(self) -> None:
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            records = await self.task_repo.list_by_run(self.run_id)
            if records and records[0].status == "running":
                return
            await asyncio.sleep(0.01)
        self.fail("running task record was not created in time")

    def _build_message(self) -> AgentMessage:
        return AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id="user-1",
            target_id="assistant-1",
            payload={"content": "hello", "session_id": self.session_id},
            run_id=self.run_id,
            session_id=self.session_id,
        )
