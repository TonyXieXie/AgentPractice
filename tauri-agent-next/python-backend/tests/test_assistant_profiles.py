from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_config import set_app_config_path
from agents.assistant import ASSISTANT_SYSTEM_PROMPT, AssistantAgent
from agents.execution import (
    AgentMemory,
    PromptIR,
    ProviderAdapter,
    ProviderTurnEvent,
    ReactStrategy,
    SimpleStrategy,
    ToolExecutor,
)
from agents.execution.directives import MESSAGE_DIRECTIVE_KINDS
from agents.instance import AgentInstance
from agents.message import AgentMessage
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.conversation_repository import ConversationRepository
from repositories.message_center_repository import MessageCenterRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from tools.base import Tool, ToolParameter, ToolRegistry


class DummyCenter:
    observer = None
    run_manager = None
    message_center_repository = None


class FakeLLMConfig:
    api_profile = "openai"


class FakeLLMClient:
    def __init__(self) -> None:
        self.config = FakeLLMConfig()


class EchoProfileTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "echo_profile"
        self.description = "Return the supplied text."
        self.parameters = [ToolParameter(name="text", type="string", description="Text")]

    async def execute(self, arguments):
        return str(arguments["text"])


class MinimalMemory:
    async def build_view(
        self,
        message,
        *,
        agent_id,
        llm_client,
        default_system_prompt,
        tool_policy_text=None,
        max_history_events=20,
        budget_cfg=None,
    ):
        return PromptIR(
            messages=[
                {"role": "developer", "content": default_system_prompt},
                {"role": "user", "content": "task"},
            ],
            budget={"phase": "build_view"},
            trace={"actions": [], "budget_runs": []},
        )

    async def ensure_budget_for_view(
        self,
        prompt_ir,
        *,
        llm_client,
        session_id,
        agent_id=None,
        run_id=None,
        phase,
        actions=None,
        iteration=None,
    ):
        return prompt_ir


class FakeProvider(ProviderAdapter):
    name = "fake"

    def __init__(self) -> None:
        self.seen_tool_names: list[str] = []

    def supports(self, llm_client) -> bool:
        return True

    def prepare_request_overrides(self, *, request_overrides, tools, llm_client):
        self.seen_tool_names = [tool.name for tool in tools]
        return dict(request_overrides)

    async def run_turn(self, *, prompt_ir, llm_client, request_overrides):
        yield ProviderTurnEvent(event_type="content", delta="final answer")
        yield ProviderTurnEvent(event_type="done", tool_calls=[])

    def append_tool_results(self, *, prompt_ir, assistant_content, tool_calls, tool_results) -> None:
        return None


class AssistantProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._temp_dir.name)
        self.config_path = self.runtime_dir / "app_config.json"
        self.store = SqliteStore(self.runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.message_center_repo = MessageCenterRepository(self.store)
        self.conversation_repo = ConversationRepository(self.store)
        self.agent_state_repo = AgentPromptStateRepository(self.store)
        self.prompt_trace_repo = PromptTraceRepository(self.store)
        self.memory = AgentMemory(
            session_repository=self.session_repo,
            message_center_repository=self.message_center_repo,
            conversation_repository=self.conversation_repo,
            agent_prompt_state_repository=self.agent_state_repo,
            prompt_trace_repository=self.prompt_trace_repo,
        )
        self.base_tool_executor = ToolExecutor(
            allowed_builtin_tool_names=MESSAGE_DIRECTIVE_KINDS
        )
        self.session_id = "session-1"
        ToolRegistry.clear()

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_profile_prompt_order_and_tool_hint_are_applied(self) -> None:
        self._write_config(
            {
                "agent": {
                    "default_profile": "worker",
                    "profiles": {
                        "worker": {
                            "extends": "default",
                            "description": "UI only description",
                            "system_prompt": "Profile prompt",
                            "tool_policy_text": "Profile tool policy",
                            "allowed_tool_names": ["send_event"],
                        }
                    },
                }
            }
        )
        await self.session_repo.create(
            session_id=self.session_id,
            system_prompt="Session prompt",
        )
        assistant = self._build_assistant(profile_id="worker")
        context = await assistant.build_execution_context(self.base_tool_executor)
        message = AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id="user-1",
            target_id=assistant.agent_id,
            payload={"content": "do work", "system_prompt": "Request prompt"},
            run_id="run-1",
            session_id=self.session_id,
        )

        prompt_ir = await self.memory.build_view(
            message,
            agent_id=assistant.agent_id,
            llm_client=FakeLLMClient(),
            default_system_prompt=context.system_prompt,
            tool_policy_text=context.tool_policy_text,
        )
        system_prompt = str(prompt_ir.messages[0]["content"])

        self.assertEqual([tool.name for tool in context.tool_executor.list_tools()], ["send_event"])
        self.assertNotIn("UI only description", system_prompt)
        self.assertLess(system_prompt.find(ASSISTANT_SYSTEM_PROMPT.strip()), system_prompt.find("Profile prompt"))
        self.assertLess(system_prompt.find("Profile prompt"), system_prompt.find("Available tools for this profile: send_event."))
        self.assertLess(
            system_prompt.find("Available tools for this profile: send_event."),
            system_prompt.find("Profile tool policy"),
        )
        self.assertLess(system_prompt.find("Profile tool policy"), system_prompt.find("Session prompt"))
        self.assertLess(system_prompt.find("Session prompt"), system_prompt.find("Request prompt"))

    async def test_simple_strategy_blocks_tools_outside_profile_allowlist(self) -> None:
        ToolRegistry.register(EchoProfileTool())
        self._write_config(
            {
                "agent": {
                    "default_profile": "worker",
                    "profiles": {
                        "worker": {
                            "extends": "default",
                            "allowed_tool_names": ["send_event"],
                        }
                    },
                }
            }
        )
        assistant = self._build_assistant(profile_id="worker")
        context = await assistant.build_execution_context(self.base_tool_executor)
        message = AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id="user-1",
            target_id=assistant.agent_id,
            payload={
                "content": "run tool",
                "request_overrides": {
                    "tool_name": "echo_profile",
                    "tool_arguments": {"text": "hello"},
                },
            },
            run_id="run-1",
            session_id=self.session_id,
        )

        strategy = SimpleStrategy()
        steps = [
            step
            async for step in strategy.execute(
                message,
                agent_id=assistant.agent_id,
                llm_client=None,
                tool_executor=self.base_tool_executor,
                memory=None,
                execution_context=context,
            )
        ]

        self.assertEqual(steps[-1].step_type, "error")
        self.assertEqual(steps[-1].content, "Tool not found: echo_profile")

    async def test_react_strategy_exposes_only_allowed_tools(self) -> None:
        ToolRegistry.register(EchoProfileTool())
        self._write_config(
            {
                "agent": {
                    "default_profile": "worker",
                    "profiles": {
                        "worker": {
                            "extends": "default",
                            "allowed_tool_names": ["send_event"],
                        }
                    },
                }
            }
        )
        assistant = self._build_assistant(profile_id="worker")
        context = await assistant.build_execution_context(self.base_tool_executor)
        provider = FakeProvider()
        strategy = ReactStrategy(providers=[provider], max_iterations=1)
        message = AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id="user-1",
            target_id=assistant.agent_id,
            payload={"content": "react"},
            run_id="run-1",
            session_id=self.session_id,
        )

        steps = [
            step
            async for step in strategy.execute(
                message,
                agent_id=assistant.agent_id,
                llm_client=FakeLLMClient(),
                tool_executor=self.base_tool_executor,
                memory=MinimalMemory(),
                execution_context=context,
            )
        ]

        self.assertEqual(provider.seen_tool_names, ["send_event"])
        self.assertEqual(steps[-1].step_type, "answer")
        self.assertEqual(steps[-1].content, "final answer")

    async def test_profile_without_allowlist_keeps_registered_tools_visible(self) -> None:
        ToolRegistry.register(EchoProfileTool())
        self._write_config(
            {
                "agent": {
                    "default_profile": "worker",
                    "profiles": {
                        "worker": {
                            "extends": "default",
                        }
                    },
                }
            }
        )
        assistant = self._build_assistant(profile_id="worker")
        context = await assistant.build_execution_context(self.base_tool_executor)

        tool_names = {tool.name for tool in context.tool_executor.list_tools()}

        self.assertIn("echo_profile", tool_names)
        self.assertIn("send_event", tool_names)

    def _build_assistant(self, *, profile_id: str) -> AssistantAgent:
        repository = AgentProfileRepository()
        instance = AgentInstance(
            id="assistant-1",
            agent_type="assistant",
            role="assistant",
            profile_id=profile_id,
        )
        return AssistantAgent(
            instance,
            DummyCenter(),
            profile_repository=repository,
        )

    def _write_config(self, payload: dict) -> None:
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")
        set_app_config_path(self.config_path)
