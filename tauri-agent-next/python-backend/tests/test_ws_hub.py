from __future__ import annotations

import asyncio

from transport.ws.ws_hub import WsHub
from transport.ws.ws_types import SubscriptionScope


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def test_ws_hub_scope_filtering() -> None:
    async def scenario() -> None:
        hub = WsHub()
        ws_a = FakeWebSocket()
        ws_b = FakeWebSocket()
        conn_a = await hub.register(ws_a)
        conn_b = await hub.register(ws_b)
        await hub.set_scope(conn_a, [SubscriptionScope(run_id="run-1")])
        await hub.set_scope(conn_b, [SubscriptionScope(agent_id="agent-2")])

        await hub.emit(
            stream="run_event",
            payload={"topic": "run.started"},
            run_id="run-1",
            agent_id="agent-1",
        )
        await hub.emit(
            stream="agent_event",
            payload={"topic": "agent.state_changed"},
            run_id="run-2",
            agent_id="agent-2",
        )

        assert len(ws_a.messages) == 1
        assert ws_a.messages[0]["payload"]["topic"] == "run.started"
        assert len(ws_b.messages) == 1
        assert ws_b.messages[0]["payload"]["topic"] == "agent.state_changed"

    asyncio.run(scenario())
