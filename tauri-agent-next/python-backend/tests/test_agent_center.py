from __future__ import annotations

import unittest

from agents.assistant import AssistantAgent
from agents.center import AgentCenter
from agents.instance import AgentInstance
from agents.user_proxy import UserProxyAgent
from observation.observer import InMemoryExecutionObserver


class AgentCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.observer = InMemoryExecutionObserver()
        self.center = AgentCenter(observer=self.observer)
        self.assistant = AssistantAgent(
            AgentInstance(
                id="assistant-1",
                agent_type="assistant",
                role="assistant",
                run_id="run-1",
            ),
            self.center,
        )
        self.assistant.register_rpc_handler(
            "task.run",
            lambda message: {
                "reply": f"echo: {message.payload.get('content', '')}",
                "handled_by": "assistant-1",
            },
        )
        self.user = UserProxyAgent(
            AgentInstance(
                id="user-1",
                agent_type="user_proxy",
                role="user_proxy",
                run_id="run-1",
            ),
            self.center,
        )
        await self.center.register(self.assistant)
        await self.center.register(self.user)

    async def test_user_proxy_can_call_assistant(self) -> None:
        response = await self.user.send_user_message(
            "hello",
            target_agent_id=self.assistant.agent_id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "echo: hello")
        self.assertEqual(response.payload["handled_by"], "assistant-1")

        snapshot = self.observer.get_snapshot("run-1")
        self.assertIsNotNone(snapshot)
        self.assertIn("assistant-1", snapshot.agents)
        self.assertGreater(snapshot.latest_seq, 0)

    async def test_unicast_event_reaches_target_agent(self) -> None:
        await self.assistant.send_event(
            "assistant.progress",
            {"step": "one"},
            target_agent_id=self.user.agent_id,
        )

        self.assertEqual(len(self.user.received_events), 1)
        self.assertEqual(self.user.received_events[0].topic, "assistant.progress")

    async def test_broadcast_event_reaches_other_agents(self) -> None:
        watcher = AssistantAgent(
            AgentInstance(
                id="assistant-2",
                agent_type="assistant",
                role="assistant",
                run_id="run-1",
            ),
            self.center,
        )
        await self.center.register(watcher)

        delivered = await self.assistant.broadcast_event(
            "workflow.updated",
            {"status": "shared"},
        )

        self.assertEqual(delivered, 2)
        self.assertEqual(len(self.user.received_events), 1)
        self.assertEqual(len(watcher.received_events), 1)
