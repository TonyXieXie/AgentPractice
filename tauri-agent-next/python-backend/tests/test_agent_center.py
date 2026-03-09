from __future__ import annotations

from copy import deepcopy
import unittest

from agents.assistant import AssistantAgent
from agents.center import AgentCenter
from agents.execution import ContextBuilder, ExecutionEngine
from agents.instance import AgentInstance
from agents.user_proxy import UserProxyAgent
from observation.observer import InMemoryExecutionObserver
from tools.base import Tool, ToolParameter, ToolRegistry


class EchoTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "echo"
        self.description = "Echo the provided text."
        self.parameters = [
            ToolParameter(name="text", type="string", description="Text to echo")
        ]

    async def execute(self, arguments):
        return arguments["text"]


class FakeLLMConfig:
    api_format = "openai_chat_completions"
    api_profile = "openai"


class FakeResponsesLLMConfig:
    api_format = "openai_responses"
    api_profile = "openai"


class FakeReactLLMClient:
    def __init__(self) -> None:
        self.config = FakeLLMConfig()
        self.calls = []

    async def chat_stream_events(self, messages, request_overrides=None):
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "request_overrides": deepcopy(request_overrides or {}),
            }
        )
        if len(self.calls) == 1:
            yield {"type": "reasoning", "delta": "Need to inspect with a tool."}
            yield {
                "type": "tool_call_delta",
                "id": "call-react-1",
                "index": 0,
                "name": "echo",
                "arguments_delta": "{\"text\":\"from react tool\"}",
            }
            yield {"type": "done", "content": ""}
            return

        yield {"type": "content", "delta": "react final answer"}
        yield {"type": "done", "content": "react final answer"}


class FakeResponsesLLMClient:
    def __init__(self) -> None:
        self.config = FakeResponsesLLMConfig()
        self.calls = []

    async def chat_stream_events(self, messages, request_overrides=None):
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "request_overrides": deepcopy(request_overrides or {}),
            }
        )
        if len(self.calls) == 1:
            yield {"type": "reasoning", "delta": "Need a tool via responses."}
            yield {
                "type": "tool_call_delta",
                "id": "call-responses-1",
                "index": 0,
                "name": "echo",
                "arguments_delta": "{\"text\":\"from responses tool\"}",
            }
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-responses-1",
                        "index": 0,
                        "name": "echo",
                        "arguments": "{\"text\":\"from responses tool\"}",
                    }
                ],
            }
            return

        yield {"type": "content", "delta": "responses final answer"}
        yield {"type": "done", "content": "responses final answer"}


class FakeContextBuilder(ContextBuilder):
    def __init__(self, llm_client) -> None:
        super().__init__()
        self._llm_client = llm_client

    def build_llm_client(self, request):
        return self._llm_client


class AgentCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        ToolRegistry.clear()
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

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()

    async def test_user_proxy_can_call_assistant(self) -> None:
        response = await self.user.send_user_message(
            "hello",
            target_agent_id=self.assistant.agent_id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "echo: hello")
        self.assertEqual(response.payload["handled_by"], "assistant-1")
        self.assertEqual(response.payload["strategy"], "simple")

        snapshot = self.observer.get_snapshot("run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.status, "completed")
        self.assertIn("assistant-1", snapshot.agents)
        self.assertGreater(snapshot.latest_seq, 0)

    async def test_engine_executes_registered_tool(self) -> None:
        ToolRegistry.register(EchoTool())

        response = await self.user.call_rpc(
            "task.run",
            {
                "content": "run echo",
                "tool_name": "echo",
                "tool_arguments": {"text": "from tool"},
            },
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "from tool")
        self.assertEqual(response.payload["tool_name"], "echo")
        self.assertTrue(response.payload["tool_call_id"])

        snapshot = self.observer.get_snapshot("run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.tool_calls), 1)
        tool_snapshot = next(iter(snapshot.tool_calls.values()))
        self.assertEqual(tool_snapshot.status, "completed")
        self.assertEqual(tool_snapshot.tool_name, "echo")

    async def test_custom_handler_still_supported(self) -> None:
        self.assistant.register_rpc_handler(
            "assistant.ping",
            lambda _message: {"reply": "pong", "handled_by": "custom"},
        )

        response = await self.user.call_rpc(
            "assistant.ping",
            {},
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "pong")
        self.assertEqual(response.payload["handled_by"], "custom")

    async def test_react_strategy_executes_tool_loop(self) -> None:
        ToolRegistry.register(EchoTool())
        fake_llm = FakeReactLLMClient()
        self.assistant.execution_engine = ExecutionEngine(
            self.assistant,
            context_builder=FakeContextBuilder(fake_llm),
        )

        response = await self.user.call_rpc(
            "task.run",
            {"content": "use react", "strategy": "react"},
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "react final answer")
        self.assertEqual(response.payload["strategy"], "react")
        self.assertEqual(len(fake_llm.calls), 2)
        self.assertIn("tools", fake_llm.calls[0]["request_overrides"])
        second_messages = fake_llm.calls[1]["messages"]
        self.assertTrue(any(msg.get("role") == "tool" for msg in second_messages))

        snapshot = self.observer.get_snapshot("run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.tool_calls), 1)
        tool_snapshot = next(iter(snapshot.tool_calls.values()))
        self.assertEqual(tool_snapshot.status, "completed")
        self.assertEqual(tool_snapshot.tool_name, "echo")

    async def test_react_strategy_supports_openai_responses_provider(self) -> None:
        ToolRegistry.register(EchoTool())
        fake_llm = FakeResponsesLLMClient()
        self.assistant.execution_engine = ExecutionEngine(
            self.assistant,
            context_builder=FakeContextBuilder(fake_llm),
        )

        response = await self.user.call_rpc(
            "task.run",
            {"content": "use responses", "strategy": "react"},
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "responses final answer")
        self.assertEqual(response.payload["strategy"], "react")
        self.assertEqual(len(fake_llm.calls), 2)
        tools_payload = fake_llm.calls[0]["request_overrides"].get("tools") or []
        self.assertTrue(tools_payload)
        self.assertEqual(tools_payload[0]["strict"], True)
        second_messages = fake_llm.calls[1]["messages"]
        self.assertTrue(
            any(message.get("type") == "function_call_output" for message in second_messages)
        )

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
