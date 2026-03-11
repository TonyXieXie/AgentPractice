from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path

from app_config import set_app_config_path
from agents.assistant import AssistantAgent
from agents.center import AgentCenter
from agents.roster_manager import AgentRosterManager
from agents.execution import ExecutionEngine, PromptIR, TaskManager, ToolExecutor
from agents.user_proxy import UserProxyAgent
from observation.observer import InMemoryExecutionObserver
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.message_center_repository import MessageCenterRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository
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

    async def chat_stream_events(self, prompt_ir, request_overrides=None):
        self.calls.append(
            {
                "messages": deepcopy(prompt_ir.messages),
                "budget": deepcopy(prompt_ir.budget),
                "trace": deepcopy(prompt_ir.trace),
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

    async def chat_stream_events(self, prompt_ir, request_overrides=None):
        self.calls.append(
            {
                "messages": deepcopy(prompt_ir.messages),
                "budget": deepcopy(prompt_ir.budget),
                "trace": deepcopy(prompt_ir.trace),
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


class FakeMemory:
    def __init__(self) -> None:
        self.build_calls = []
        self.ensure_calls = []

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
        prompt_ir = PromptIR(
            messages=[{"role": "user", "content": str(message.payload.get("content") or "")}],
            budget={
                "max_context_tokens": 4096,
                "prompt_budget": 3584,
                "estimated_prompt_tokens": 12,
                "phase": "build_view",
            },
            trace={"actions": [], "budget_runs": []},
        )
        self.build_calls.append(
            {
                "agent_id": agent_id,
                "default_system_prompt": default_system_prompt,
                "max_history_events": max_history_events,
                "messages": deepcopy(prompt_ir.messages),
            }
        )
        return prompt_ir

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
        prompt_ir.budget = {
            "max_context_tokens": 4096,
            "prompt_budget": 3584,
            "estimated_prompt_tokens": len(prompt_ir.messages) * 10,
            "phase": phase,
        }
        if iteration is not None:
            prompt_ir.budget["iteration"] = iteration
        self.ensure_calls.append(
            {
                "phase": phase,
                "iteration": iteration,
                "messages": deepcopy(prompt_ir.messages),
            }
        )
        return prompt_ir


class DummyRunManager:
    async def get_active_run_by_session(self, _session_id: str):
        return None

    async def open_run(self, *_args, **_kwargs):
        raise RuntimeError("not used in AgentCenterTests")

    async def attach_root_task(self, *_args, **_kwargs):
        return None

    async def finish_run(self, *_args, **_kwargs):
        return None

    async def fail_run(self, *_args, **_kwargs):
        return None

    async def stop_run(self, *_args, **_kwargs):
        return None


class AgentCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        ToolRegistry.clear()
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        config_path = runtime_dir / "app_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agent": {
                        "default_profile": "default",
                        "profiles": {
                            "default": {
                                "agent_type": "assistant",
                                "display_name": "Assistant",
                                "subscribed_topics": ["workflow.updated"],
                                "executable_event_topics": [],
                            },
                            "worker": {
                                "agent_type": "assistant",
                                "display_name": "Worker",
                                "subscribed_topics": ["workflow.updated"],
                                "executable_event_topics": ["worker.execute"],
                            },
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        set_app_config_path(config_path)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.agent_instance_repository = AgentInstanceRepository(self.store)
        self.agent_profile_repository = AgentProfileRepository()
        self.message_center_repository = MessageCenterRepository(self.store)
        self.task_repository = TaskRepository(self.store)
        self.task_manager = TaskManager(self.task_repository)
        self.session_id = "session-1"
        self.run_id = "run-1"
        await self.session_repo.create(session_id=self.session_id)
        self.observer = InMemoryExecutionObserver()
        self.center = AgentCenter(
            observer=self.observer,
            message_center_repository=self.message_center_repository,
            session_repository=self.session_repo,
        )
        self.roster_manager = AgentRosterManager(
            agent_center=self.center,
            agent_instance_repository=self.agent_instance_repository,
            agent_profile_repository=self.agent_profile_repository,
            memory=None,
            tool_executor=ToolExecutor(),
            task_manager=self.task_manager,
        )
        self.center.roster_manager = self.roster_manager
        self.roster_manager.bind_runtime_services(
            run_manager=DummyRunManager(),
        )
        self.memory = FakeMemory()
        self.user_record = await self.agent_instance_repository.get_or_create_primary(
            self.session_id,
            "user_proxy",
            profile_id=None,
            display_name="UserProxy",
        )
        self.assistant_record = await self.agent_instance_repository.get_or_create_primary(
            self.session_id,
            "assistant",
            profile_id="default",
            display_name="Assistant",
        )
        self.runtime_agents = await self.roster_manager.hydrate_run_roster(
            self.session_id,
            self.run_id,
        )
        self.user = await self.roster_manager.ensure_primary_user_proxy(self.session_id)
        self.assistant = self.runtime_agents[self.assistant_record.id]
        assert isinstance(self.assistant, AssistantAgent)
        assert isinstance(self.user, UserProxyAgent)
        self.assistant.execution_engine = ExecutionEngine(self.assistant, memory=self.memory)

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()
        await self.roster_manager.shutdown()
        await self.center.drain()
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_user_proxy_can_call_assistant(self) -> None:
        request = await self.user.send_user_message(
            "hello",
            target_agent_id=self.assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        response = await self._wait_for_response_message(
            self.user.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "echo: hello")
        self.assertEqual(response.payload["handled_by"], self.assistant.agent_id)
        self.assertEqual(response.payload["strategy"], "simple")
        tasks = await self.task_repository.list_by_run("run-1")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "completed")
        llm_events = [
            event
            for event in self.observer.list_events(run_id="run-1")
            if event.event_type == "llm.updated"
        ]
        self.assertTrue(llm_events)
        self.assertEqual(llm_events[-1].metadata.get("task_id"), tasks[0].id)

        snapshot = self.observer.get_snapshot("run-1")
        self.assertIsNotNone(snapshot)
        self.assertIn(self.assistant.agent_id, snapshot.agents)
        self.assertGreater(snapshot.latest_seq, 0)
        event_types = [event.event_type for event in self.observer.list_events(run_id="run-1")]
        self.assertNotIn("run.started", event_types)
        self.assertNotIn("run.finished", event_types)
        user_visible = await self.message_center_repository.list_latest_visible(
            self.session_id,
            self.user.agent_id,
            limit=10,
        )
        assistant_visible = await self.message_center_repository.list_latest_visible(
            self.session_id,
            self.assistant.agent_id,
            limit=10,
        )
        self.assertEqual([record.kind for record in user_visible], ["rpc_request", "rpc_response"])
        self.assertEqual([record.kind for record in assistant_visible], ["rpc_request", "rpc_response"])
        self.assertEqual(len({record.message_id for record in user_visible}), 2)
        self.assertEqual(len({record.message_id for record in assistant_visible}), 2)

    async def test_engine_executes_registered_tool(self) -> None:
        ToolRegistry.register(EchoTool())

        request = await self.user.send_user_message(
            "run echo",
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
            session_id=self.session_id,
            request_overrides={
                "tool_name": "echo",
                "tool_arguments": {"text": "from tool"},
            },
        )
        response = await self._wait_for_response_message(
            self.user.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "from tool")
        self.assertEqual(response.payload["tool_name"], "echo")
        self.assertTrue(response.payload["tool_call_id"])
        tasks = await self.task_repository.list_by_run("run-1")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "completed")

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

        request = await self.user.send_rpc_request(
            "assistant.ping",
            {},
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
            session_id=self.session_id,
        )
        response = await self._wait_for_response_message(
            self.user.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "pong")
        self.assertEqual(response.payload["handled_by"], "custom")

    async def test_react_strategy_executes_tool_loop(self) -> None:
        ToolRegistry.register(EchoTool())
        fake_llm = FakeReactLLMClient()
        self.assistant.execution_engine = ExecutionEngine(
            self.assistant,
            memory=self.memory,
            llm_client_factory=lambda _request: fake_llm,
        )

        request = await self.user.send_user_message(
            "use react",
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
            session_id=self.session_id,
            strategy="react",
        )
        response = await self._wait_for_response_message(
            self.user.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.payload["reply"], "react final answer")
        self.assertEqual(response.payload["strategy"], "react")
        self.assertEqual(len(fake_llm.calls), 2)
        self.assertIn("tools", fake_llm.calls[0]["request_overrides"])
        second_messages = fake_llm.calls[1]["messages"]
        self.assertTrue(any(msg.get("role") == "tool" for msg in second_messages))
        self.assertEqual(len(self.memory.ensure_calls), 1)
        self.assertEqual(self.memory.ensure_calls[0]["phase"], "react_iteration")

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
            memory=self.memory,
            llm_client_factory=lambda _request: fake_llm,
        )

        request = await self.user.send_user_message(
            "use responses",
            target_agent_id=self.assistant.agent_id,
            run_id="run-1",
            session_id=self.session_id,
            strategy="react",
        )
        response = await self._wait_for_response_message(
            self.user.agent_id,
            request.correlation_id or request.id,
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
        watcher = await self._create_runtime_assistant(profile_id="default")

        delivered = await self.assistant.broadcast_event(
            "workflow.updated",
            {"status": "shared"},
        )

        self.assertEqual(delivered, 1)
        self.assertEqual(len(self.user.received_events), 0)
        self.assertEqual(len(watcher.received_events), 1)

    async def test_send_rpc_request_returns_response_and_redelivers_response_to_target_agent(self) -> None:
        watcher = await self._create_runtime_assistant(profile_id="default")

        request = await self.assistant.send_rpc_request(
            "assistant.delegate",
            {
                "content": "delegate this",
                "session_id": self.session_id,
            },
            target_agent_id=watcher.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        response = await self._wait_for_response_message(
            self.assistant.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.target_id, self.assistant.agent_id)
        tasks = await self._wait_for_tasks_count(2)
        self.assertEqual(tasks[0].agent_id, watcher.agent_id)
        self.assertEqual(tasks[0].source_message_kind, "rpc_request")
        self.assertEqual(tasks[1].agent_id, self.assistant.agent_id)
        self.assertEqual(tasks[1].source_message_kind, "rpc_response")

        assistant_visible = await self.message_center_repository.list_latest_visible(
            self.session_id,
            self.assistant.agent_id,
            limit=10,
        )
        self.assertEqual([record.kind for record in assistant_visible], ["rpc_request", "rpc_response"])
        self.assertEqual(len({record.message_id for record in assistant_visible}), 2)

    async def test_rpc_response_without_waiter_still_reaches_target_agent(self) -> None:
        watcher = await self._create_runtime_assistant(profile_id="default")

        request = await self.assistant.send_rpc_request(
            "assistant.delegate",
            {
                "content": "delegate without waiter",
                "session_id": self.session_id,
            },
            target_agent_id=watcher.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )

        self.assertEqual(request.kind, "rpc_request")
        tasks = await self._wait_for_tasks_count(2)
        self.assertEqual(tasks[0].agent_id, watcher.agent_id)
        self.assertEqual(tasks[1].agent_id, self.assistant.agent_id)
        self.assertEqual(tasks[1].source_message_kind, "rpc_response")

        assistant_visible = await self.message_center_repository.list_latest_visible(
            self.session_id,
            self.assistant.agent_id,
            limit=10,
        )
        self.assertEqual([record.kind for record in assistant_visible], ["rpc_request", "rpc_response"])
        self.assertEqual(len({record.message_id for record in assistant_visible}), 2)

    async def test_profile_routed_rpc_reuses_existing_idle_instance(self) -> None:
        worker = await self._create_runtime_assistant(profile_id="worker")

        request = await self.assistant.send_rpc_request(
            "assistant.delegate",
            {
                "content": "delegate by profile",
                "session_id": self.session_id,
            },
            target_profile="worker",
            run_id=self.run_id,
            session_id=self.session_id,
        )
        response = await self._wait_for_response_message(
            self.assistant.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        tasks = await self._wait_for_tasks_count(2)
        self.assertEqual(tasks[0].agent_id, worker.agent_id)
        records = await self.agent_instance_repository.list_by_session(self.session_id)
        worker_records = [record for record in records if record.profile_id == "worker"]
        self.assertEqual(len(worker_records), 1)
        assistant_visible = await self.message_center_repository.list_latest_visible(
            self.session_id,
            self.assistant.agent_id,
            limit=10,
        )
        self.assertEqual(assistant_visible[0].target_profile, "worker")
        self.assertEqual(assistant_visible[0].target_id, worker.agent_id)

    async def test_profile_routed_rpc_creates_new_instance_when_profile_is_busy(self) -> None:
        busy_worker = await self._create_runtime_assistant(profile_id="worker")
        busy_worker.instance = busy_worker.instance.with_status("running")
        records_before = await self.agent_instance_repository.list_by_session(self.session_id)

        request = await self.assistant.send_rpc_request(
            "assistant.delegate",
            {
                "content": "delegate to busy profile",
                "session_id": self.session_id,
            },
            target_profile="worker",
            run_id=self.run_id,
            session_id=self.session_id,
        )

        self.assertEqual(request.target_profile, "worker")
        tasks = await self._wait_for_tasks_count(2)
        self.assertNotEqual(tasks[0].agent_id, busy_worker.agent_id)
        records_after = await self.agent_instance_repository.list_by_session(self.session_id)
        worker_records = [record for record in records_after if record.profile_id == "worker"]
        self.assertEqual(len(records_after), len(records_before) + 1)
        self.assertEqual(len(worker_records), 2)
        self.assertIn(tasks[0].agent_id, self.runtime_agents)

    async def test_target_id_overrides_target_profile(self) -> None:
        worker = await self._create_runtime_assistant(profile_id="worker")

        request = await self.assistant.send_rpc_request(
            "assistant.delegate",
            {
                "content": "explicit target wins",
                "session_id": self.session_id,
            },
            target_agent_id=worker.agent_id,
            target_profile="missing-profile",
            run_id=self.run_id,
            session_id=self.session_id,
        )
        response = await self._wait_for_response_message(
            self.assistant.agent_id,
            request.correlation_id or request.id,
        )

        self.assertTrue(response.ok)
        tasks = await self._wait_for_tasks_count(2)
        self.assertEqual(tasks[0].agent_id, worker.agent_id)

    async def test_unknown_target_profile_raises_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Agent profile not found: missing-profile"):
            await self.assistant.send_rpc_request(
                "assistant.delegate",
                {
                    "content": "delegate to missing profile",
                    "session_id": self.session_id,
                },
                target_profile="missing-profile",
                run_id=self.run_id,
                session_id=self.session_id,
            )

    async def test_executable_event_hosts_task_for_profile(self) -> None:
        await self.assistant.send_event(
            "worker.execute",
            {
                "content": "execute from event",
                "session_id": self.session_id,
            },
            target_profile="worker",
            run_id=self.run_id,
            session_id=self.session_id,
        )

        tasks = await self._wait_for_tasks_count(1)
        self.assertEqual(tasks[0].source_message_kind, "event")
        self.assertEqual(tasks[0].topic, "worker.execute")
        worker_agents = [
            agent
            for agent in self.runtime_agents.values()
            if isinstance(agent, AssistantAgent) and agent.instance.profile_id == "worker"
        ]
        self.assertEqual(len(worker_agents), 1)
        self.assertEqual(len(worker_agents[0].received_events), 1)

    async def test_non_executable_event_only_notifies_target(self) -> None:
        worker = await self._create_runtime_assistant(profile_id="worker")

        await self.assistant.send_event(
            "worker.notice",
            {
                "content": "notify only",
                "session_id": self.session_id,
            },
            target_agent_id=worker.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )

        await asyncio.sleep(0.05)
        tasks = await self.task_repository.list_by_run(self.run_id)
        self.assertEqual(tasks, [])
        self.assertEqual(len(worker.received_events), 1)

    async def _wait_for_tasks_count(self, expected: int) -> list:
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            tasks = await self.task_repository.list_by_run(self.run_id)
            if len(tasks) >= expected:
                return tasks
            await asyncio.sleep(0.01)
        self.fail(f"expected at least {expected} tasks")

    async def _wait_for_response_message(self, viewer_agent_id: str, correlation_id: str):
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            records = await self.message_center_repository.list_latest_visible(
                self.session_id,
                viewer_agent_id,
                limit=20,
            )
            for record in reversed(records):
                if (
                    record.kind == "rpc_response"
                    and record.correlation_id == correlation_id
                ):
                    return record
            await asyncio.sleep(0.01)
        self.fail(f"expected rpc_response for correlation_id={correlation_id}")

    async def _create_runtime_assistant(self, *, profile_id: str) -> AssistantAgent:
        record = await self.agent_instance_repository.create(
            session_id=self.session_id,
            agent_type="assistant",
            profile_id=profile_id,
            role="assistant",
            display_name=f"{profile_id}-assistant",
        )
        agent = self.roster_manager._instantiate_agent(record, run_id=self.run_id)
        assert isinstance(agent, AssistantAgent)
        agent.execution_engine = ExecutionEngine(agent, memory=self.memory)
        await self.roster_manager.attach_runtime_agent(self.run_id, agent)
        return agent
