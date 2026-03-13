from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app_services import build_app_services
from observation.facts import ObservationScope
from repositories.session_repository import SessionRepository


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.services = build_app_services(data_dir=Path(self._temp_dir.name))
        await self.services.startup()
        self.session_repository = SessionRepository(self.services.sqlite_store)
        self.session_id = "session-1"
        await self.session_repository.create(session_id=self.session_id)
        self.primary_assistant = (
            await self.services.agent_roster_manager.ensure_primary_entry_assistant(self.session_id)
        )

    async def asyncTearDown(self) -> None:
        await self.services.shutdown()
        self._temp_dir.cleanup()

    async def test_open_run_and_finish_run_persist_run_lifecycle_facts(self) -> None:
        active_run = await self.services.run_manager.open_run(
            self.session_id,
            "user-1",
            entry_assistant_id=self.primary_assistant.id,
            metadata={"strategy": "simple", "user_agent_id": "user-1"},
        )
        await self.services.run_manager.finish_run(
            active_run.run_id,
            {"reply": "done", "status": "completed", "strategy": "simple"},
        )

        facts = await self.services.observation_center.list_shared(
            ObservationScope(session_id=self.session_id, run_id=active_run.run_id),
            after_seq=0,
            limit=20,
        )
        self.assertEqual([fact.topic for fact in facts], ["run.started", "run.finished"])
        self.assertEqual(facts[0].payload["controller_agent_id"], "user-1")
        self.assertEqual(facts[1].payload["status"], "completed")
        registered_after = await self.services.agent_center.list_agents()
        self.assertEqual(registered_after, [])

    async def test_stop_run_persists_stopped_status_and_returns_stopping(self) -> None:
        active_run = await self.services.run_manager.open_run(
            self.session_id,
            "user-1",
            entry_assistant_id=self.primary_assistant.id,
            metadata={"strategy": "simple", "user_agent_id": "user-1"},
        )
        blocker = asyncio.Event()
        root_task = asyncio.create_task(blocker.wait())
        await self.services.run_manager.attach_root_task(active_run.run_id, root_task)

        stop_response = await self.services.run_manager.stop_run(active_run.run_id)
        self.assertIsNotNone(stop_response)
        assert stop_response is not None
        self.assertEqual(stop_response.status, "stopping")

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(root_task, timeout=1.0)

        facts = await self.services.observation_center.list_shared(
            ObservationScope(session_id=self.session_id, run_id=active_run.run_id),
            after_seq=0,
            limit=20,
        )
        self.assertEqual(facts[-1].topic, "run.finished")
        self.assertEqual(facts[-1].payload["status"], "stopped")
