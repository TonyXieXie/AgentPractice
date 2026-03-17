import copy
import os
import sys
import unittest
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.react import ReActAgent  # noqa: E402


class CapturingLLMClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            api_profile="openai",
            api_format="openai_chat_completions",
            model="gpt-4.1-mini",
            temperature=0,
            max_tokens=256,
            max_context_tokens=0,
        )
        self.requests = []

    async def chat_stream_events(self, messages, request_overrides=None):
        self.requests.append(
            {
                "messages": copy.deepcopy(messages),
                "request_overrides": copy.deepcopy(request_overrides or {}),
            }
        )
        yield {"type": "done", "content": "ok", "tool_calls": []}


class ReActAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_history_is_sent_after_current_user(self) -> None:
        llm_client = CapturingLLMClient()
        agent = ReActAgent(max_iterations=1, system_prompt="system")

        steps = [
            step
            async for step in agent.execute(
                user_input="ship feature",
                history=[{"role": "assistant", "content": "[Planner] outlined plan"}],
                tools=[],
                llm_client=llm_client,
                request_overrides={"_graph_history_after_user": True},
            )
        ]

        self.assertEqual(steps[-1].step_type, "answer")
        self.assertEqual(steps[-1].content, "ok")
        messages = llm_client.requests[0]["messages"]
        self.assertEqual([message["role"] for message in messages[:3]], ["developer", "user", "assistant"])
        self.assertEqual(messages[1]["content"], "ship feature")
        self.assertEqual(messages[2]["content"], "[Planner] outlined plan")

    async def test_non_graph_history_keeps_default_order(self) -> None:
        llm_client = CapturingLLMClient()
        agent = ReActAgent(max_iterations=1, system_prompt="system")

        steps = [
            step
            async for step in agent.execute(
                user_input="ship feature",
                history=[{"role": "assistant", "content": "[Planner] outlined plan"}],
                tools=[],
                llm_client=llm_client,
                request_overrides={},
            )
        ]

        self.assertEqual(steps[-1].step_type, "answer")
        self.assertEqual(steps[-1].content, "ok")
        messages = llm_client.requests[0]["messages"]
        self.assertEqual([message["role"] for message in messages[:3]], ["developer", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "[Planner] outlined plan")
        self.assertEqual(messages[2]["content"], "ship feature")


if __name__ == "__main__":
    unittest.main()
