from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from observation.center import ObservationCenter
from observation.facts import ObservationScope
from observation.query_service import FactQueryService
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore


class ObservationCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.store = SqliteStore(Path(self._temp_dir.name) / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        await self.session_repo.create(session_id="session-1")
        self.shared_repo = SharedFactRepository(self.store)
        self.private_repo = AgentPrivateEventRepository(self.store)
        self.query_service = FactQueryService(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
        )
        self.center = ObservationCenter(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
            fact_query_service=self.query_service,
        )

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_shared_facts_append_and_list_in_fact_seq_order(self) -> None:
        fact_a = await self.center.append_shared_fact(
            session_id="session-1",
            run_id="run-1",
            message_id="msg-1",
            sender_id="UserProxy",
            target_agent_id="Planner",
            topic="task.plan",
            fact_type="event_handoff",
            payload_json={"content": "交给 Planner"},
        )
        fact_b = await self.center.append_shared_fact(
            session_id="session-1",
            run_id="run-1",
            message_id="msg-2",
            sender_id="Planner",
            target_agent_id="Coder",
            topic="task.code",
            fact_type="event_handoff",
            payload_json={"content": "交给 Coder"},
        )

        facts = await self.center.list_shared(
            ObservationScope(session_id="session-1", run_id="run-1"),
            after_seq=0,
            limit=10,
        )

        self.assertEqual([fact.fact_seq for fact in facts], [fact_a.fact_seq, fact_b.fact_seq])
        self.assertEqual([fact.topic for fact in facts], ["task.plan", "task.code"])

    async def test_private_events_append_and_filter_by_agent_and_trigger_fact(self) -> None:
        trigger_fact = await self.center.append_shared_fact(
            session_id="session-1",
            run_id="run-1",
            message_id="msg-1",
            sender_id="UserProxy",
            target_agent_id="Planner",
            topic="task.plan",
            fact_type="event_handoff",
            payload_json={"content": "交给 Planner"},
        )
        event_a = await self.center.append_private_event(
            session_id="session-1",
            owner_agent_id="Planner",
            run_id="run-1",
            message_id="msg-1",
            trigger_fact_id=trigger_fact.fact_id,
            kind="tool_call",
            payload_json={"tool_name": "read_file"},
        )
        event_b = await self.center.append_private_event(
            session_id="session-1",
            owner_agent_id="Planner",
            run_id="run-1",
            message_id="msg-1",
            trigger_fact_id=trigger_fact.fact_id,
            kind="tool_result",
            payload_json={"tool_name": "read_file", "ok": True},
        )
        await self.center.append_private_event(
            session_id="session-1",
            owner_agent_id="Coder",
            run_id="run-1",
            message_id="msg-2",
            kind="tool_call",
            payload_json={"tool_name": "write_file"},
        )

        events = await self.center.list_private(
            ObservationScope(
                session_id="session-1",
                run_id="run-1",
                agent_id="Planner",
                include_private=True,
            ),
            after_id=0,
            limit=10,
        )

        self.assertEqual(
            [event.private_event_id for event in events],
            [event_a.private_event_id, event_b.private_event_id],
        )

        filtered = await self.private_repo.list(
            "session-1",
            owner_agent_id="Planner",
            trigger_fact_id=trigger_fact.fact_id,
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in filtered], ["tool_call", "tool_result"])
