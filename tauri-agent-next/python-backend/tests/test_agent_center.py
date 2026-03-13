from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.base import AgentBase
from agents.center import AgentCenter
from agents.message import AgentMessage
from observation.center import ObservationCenter
from observation.facts import ObservationScope
from observation.query_service import FactQueryService
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore


class PassiveAgent(AgentBase):
    def __init__(self, *args, responder=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_messages: list[AgentMessage] = []
        self._responder = responder

    async def on_message(self, message: AgentMessage):
        self.received_messages.append(message)
        if self._responder is not None:
            return await self._responder(message)
        return None


class AgentCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.shared_repo = SharedFactRepository(self.store)
        self.private_repo = AgentPrivateEventRepository(self.store)
        self.query_service = FactQueryService(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
        )
        self.observation_center = ObservationCenter(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
            fact_query_service=self.query_service,
        )
        self.center = AgentCenter(
            observation_center=self.observation_center,
            session_repository=self.session_repo,
        )
        self.session_id = "session-1"
        self.run_id = "run-1"
        await self.session_repo.create(session_id=self.session_id)

    async def asyncTearDown(self) -> None:
        await self.center.drain()
        self._temp_dir.cleanup()

    async def test_routing_rpc_request_creates_shared_fact_and_injects_trigger_fact_id(self) -> None:
        sender = PassiveAgent(
            instance=self._instance("sender"),
            center=self.center,
            observer=self.observation_center,
        )

        async def responder(message: AgentMessage):
            return AgentMessage.build_rpc_response(
                request=message,
                sender_id="receiver",
                payload={"reply": "pong"},
                ok=True,
            )

        receiver = PassiveAgent(
            instance=self._instance("receiver"),
            center=self.center,
            observer=self.observation_center,
            responder=responder,
        )
        await self.center.register(sender)
        await self.center.register(receiver)

        request = AgentMessage.build_rpc_request(
            topic="assistant.ping",
            sender_id=sender.agent_id,
            target_id=receiver.agent_id,
            payload={"content": "ping"},
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.route(request)
        await self.center.drain()

        facts = await self.observation_center.list_shared(
            ObservationScope(session_id=self.session_id, run_id=self.run_id),
            after_seq=0,
            limit=10,
        )
        self.assertEqual([fact.fact_type for fact in facts], ["rpc_request", "rpc_response"])
        delivered_request = receiver.received_messages[0]
        self.assertTrue(delivered_request.metadata.get("trigger_fact_id"))
        self.assertEqual(
            delivered_request.metadata.get("trigger_fact_id"),
            facts[0].fact_id,
        )

    async def test_event_handoff_is_persisted_once_before_delivery(self) -> None:
        sender = PassiveAgent(
            instance=self._instance("user_proxy"),
            center=self.center,
            observer=self.observation_center,
        )
        receiver = PassiveAgent(
            instance=self._instance("planner"),
            center=self.center,
            observer=self.observation_center,
        )
        await self.center.register(sender)
        await self.center.register(receiver)

        event = AgentMessage.build_event(
            topic="task.plan",
            sender_id=sender.agent_id,
            target_id=receiver.agent_id,
            payload={"content": "交给 Planner"},
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.route(event)

        facts = await self.shared_repo.list(
            self.session_id,
            after_seq=0,
            limit=10,
            run_id=self.run_id,
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].fact_type, "event_handoff")
        self.assertEqual(receiver.received_messages[0].metadata["trigger_fact_id"], facts[0].fact_id)

    def _instance(self, agent_id: str):
        from agents.instance import AgentInstance

        return AgentInstance(id=agent_id, agent_type="assistant", role="assistant")
