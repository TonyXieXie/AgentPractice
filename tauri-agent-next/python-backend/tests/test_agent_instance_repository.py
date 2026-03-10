from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore


class AgentInstanceRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(self.runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.agent_repo = AgentInstanceRepository(self.store)
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_get_or_create_primary_is_idempotent(self) -> None:
        assistant1 = await self.agent_repo.get_or_create_primary(self.session_id, "assistant", display_name="Assistant")
        assistant2 = await self.agent_repo.get_or_create_primary(self.session_id, "assistant", display_name="Assistant")
        self.assertEqual(assistant1.id, assistant2.id)

        user1 = await self.agent_repo.get_or_create_primary(self.session_id, "user_proxy", display_name="UserProxy")
        user2 = await self.agent_repo.get_or_create_primary(self.session_id, "user_proxy", display_name="UserProxy")
        self.assertEqual(user1.id, user2.id)
        self.assertNotEqual(user1.id, assistant1.id)

        records = await self.agent_repo.list_by_session(self.session_id)
        self.assertEqual({record.agent_type for record in records}, {"assistant", "user_proxy"})

