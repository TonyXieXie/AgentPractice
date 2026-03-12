from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from app_config import set_app_config_path
from agents.assistant import AssistantAgent
from agents.base import AgentBase
from agents.center import AgentCenter
from agents.execution import ExecutionEngine, PromptIR, TaskManager, ToolExecutor
from agents.execution.message_utils import render_current_message
from agents.instance import AgentInstance
from agents.message import AgentMessage
from agents.roster_manager import AgentRosterManager
from agents.user_proxy import UserProxyAgent
from observation.observer import InMemoryExecutionObserver
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.conversation_repository import ConversationRepository
from repositories.message_center_repository import MessageCenterRepository, SHARED_VIEWER_AGENT_ID
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository


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
            messages=[{"role": "user", "content": render_current_message(message)}],
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
        prompt_ir.budget = {
            "phase": phase,
            "estimated_prompt_tokens": len(prompt_ir.messages) * 10,
        }
        if iteration is not None:
            prompt_ir.budget["iteration"] = iteration
        return prompt_ir


class FakeResponsesLLMConfig:
    api_format = "openai_responses"
    api_profile = "openai"


class SendRpcResponseLLM:
    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.config = FakeResponsesLLMConfig()

    async def chat_stream_events(self, prompt_ir, request_overrides=None):
        text = "\n".join(str(message.get("content") or "") for message in prompt_ir.messages)
        match = re.search(r"id=([0-9a-f]+)", text)
        if match is None:
            raise AssertionError(f"message id missing from prompt: {text}")
        reply_to_message_id = match.group(1)
        arguments = {
            "reply_to_message_id": reply_to_message_id,
            "payload": {"reply": self.reply},
            "ok": True,
        }
        yield {
            "type": "tool_call_delta",
            "id": "call-response-1",
            "index": 0,
            "name": "send_rpc_response",
            "arguments_delta": json.dumps(arguments),
        }
        yield {
            "type": "done",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-response-1",
                    "index": 0,
                    "name": "send_rpc_response",
                    "arguments": json.dumps(arguments),
                }
            ],
        }


class PassiveAgent(AgentBase):
    def __init__(self, *args, responder=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_messages: list[AgentMessage] = []
        self.received_events: list[AgentMessage] = []
        self._responder = responder

    async def on_message(self, message: AgentMessage):
        self.received_messages.append(message)
        if message.message_type == "event":
            self.received_events.append(message)
        if self._responder is not None:
            return await self._responder(message)
        return None


class DummyRunManager:
    def __init__(self) -> None:
        self._active_runs: dict[str, Any] = {}

    async def get_active_run_by_session(self, _session_id: str):
        return None

    async def get_active_run(self, run_id: str):
        return self._active_runs.get(run_id)

    def register_active_run(self, run_id: str, *, controller_agent_id: str) -> None:
        self._active_runs[run_id] = type(
            "ActiveRunStub",
            (),
            {"run_id": run_id, "controller_agent_id": controller_agent_id},
        )()

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
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        config_path = runtime_dir / "app_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agent": {
                        "default_profile": "assistant_default",
                        "profiles": {
                            "assistant_default": {
                                "extends": "default",
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
        self.conversation_repository = ConversationRepository(self.store)
        self.agent_prompt_state_repository = AgentPromptStateRepository(self.store)
        self.prompt_trace_repository = PromptTraceRepository(self.store)
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
        self.run_manager = DummyRunManager()
        self.center.run_manager = self.run_manager
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
            run_manager=self.run_manager,
        )

    async def asyncTearDown(self) -> None:
        await self.roster_manager.shutdown()
        await self.center.drain()
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_shared_log_persists_each_message_once(self) -> None:
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        receiver = AssistantAgent(
            AgentInstance(id="receiver", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        receiver.register_rpc_handler("assistant.ping", lambda _message: {"reply": "pong"})
        await self.center.register(sender)
        await self.center.register(receiver)

        await sender.send_rpc_request(
            "assistant.ping",
            {},
            target_agent_id=receiver.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        shared = await self.message_center_repository.list_latest_shared(
            self.session_id,
            limit=10,
        )
        sender_view = await self.message_center_repository.list_latest_visible(
            self.session_id,
            sender.agent_id,
            limit=10,
        )
        receiver_view = await self.message_center_repository.list_latest_visible(
            self.session_id,
            receiver.agent_id,
            limit=10,
        )

        self.assertEqual([record.kind for record in shared], ["rpc_request", "rpc_response"])
        self.assertEqual([record.viewer_agent_id for record in shared], [SHARED_VIEWER_AGENT_ID] * 2)
        self.assertEqual([record.message_id for record in sender_view], [record.message_id for record in shared])
        self.assertEqual([record.message_id for record in receiver_view], [record.message_id for record in shared])

    async def test_assistant_missing_directive_auto_replies_protocol_error_for_rpc_request(self) -> None:
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.execution_engine = ExecutionEngine(assistant, memory=None, tool_executor=ToolExecutor())
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_rpc_request(
            "task.run",
            {"content": "hello"},
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        shared = await self.message_center_repository.list_latest_shared(self.session_id, limit=10)
        tasks = await self.task_repository.list_by_run(self.run_id)
        responses = [
            message
            for message in sender.received_messages
            if message.message_type == "rpc" and message.rpc_phase == "response"
        ]
        self.assertEqual([record.kind for record in shared], ["rpc_request", "rpc_response"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(responses), 1)
        self.assertFalse(responses[0].ok)
        self.assertEqual(responses[0].payload["status"], "protocol_error")
        protocol_errors = [
            event
            for event in self.observer.list_events(agent_id=assistant.agent_id)
            if event.event_type == "assistant.protocol_error"
        ]
        self.assertTrue(protocol_errors)

    async def test_assistant_missing_directive_on_event_routes_protocol_error_to_controller(self) -> None:
        controller = PassiveAgent(
            AgentInstance(id="user-1", agent_type="user_proxy", role="user_proxy"),
            self.center,
            observer=self.observer,
        )
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.execution_engine = ExecutionEngine(assistant, memory=None, tool_executor=ToolExecutor())
        self.run_manager.register_active_run(self.run_id, controller_agent_id=controller.agent_id)
        await self.center.register(controller)
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_event(
            "worker.execute",
            {"content": "do work"},
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        controller_events = [message for message in controller.received_events if message.topic == "agent.protocol_error"]
        self.assertEqual(len(controller_events), 1)
        self.assertEqual(controller_events[0].payload["offending_agent_id"], assistant.agent_id)
        self.assertEqual(controller_events[0].payload["source_message_type"], "event")
        self.assertEqual(controller_events[0].payload["source_topic"], "worker.execute")

    async def test_assistant_missing_directive_on_rpc_response_routes_protocol_error_to_controller(self) -> None:
        controller = PassiveAgent(
            AgentInstance(id="user-1", agent_type="user_proxy", role="user_proxy"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.execution_engine = ExecutionEngine(assistant, memory=None, tool_executor=ToolExecutor())
        self.run_manager.register_active_run(self.run_id, controller_agent_id=controller.agent_id)
        await self.center.register(controller)
        await self.center.register(assistant)

        request = AgentMessage.build_rpc_request(
            topic="task.run",
            sender_id=assistant.agent_id,
            target_id="worker-1",
            payload={"content": "delegate"},
            run_id=self.run_id,
            session_id=self.session_id,
        )
        response = AgentMessage.build_rpc_response(
            request=request,
            sender_id="worker-1",
            payload={"reply": "worker finished"},
            ok=True,
        )
        await self.center.route(response)
        await self.center.drain()

        controller_events = [message for message in controller.received_events if message.topic == "agent.protocol_error"]
        self.assertEqual(len(controller_events), 1)
        self.assertEqual(controller_events[0].payload["offending_agent_id"], assistant.agent_id)
        self.assertEqual(controller_events[0].payload["source_message_type"], "rpc")
        self.assertEqual(controller_events[0].payload["source_rpc_phase"], "response")

    async def test_assistant_can_explicitly_send_rpc_response_via_control_tool(self) -> None:
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.execution_engine = ExecutionEngine(
            assistant,
            memory=MinimalMemory(),
            tool_executor=ToolExecutor(),
            llm_client_factory=lambda _message: SendRpcResponseLLM(reply="pong"),
        )
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_rpc_request(
            "task.run",
            {"content": "hello", "strategy": "react"},
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        responses = [
            message
            for message in sender.received_messages
            if message.message_type == "rpc" and message.rpc_phase == "response"
        ]
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].ok)
        self.assertEqual(responses[0].payload["reply"], "pong")

    async def test_assistant_cannot_use_terminal_directive_tools(self) -> None:
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.execution_engine = ExecutionEngine(assistant, memory=None, tool_executor=ToolExecutor())
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_rpc_request(
            "task.run",
            {
                "content": "hello",
                "request_overrides": {
                    "tool_name": "finish_run",
                    "tool_arguments": {"reply": "not allowed"},
                },
            },
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        responses = [
            message
            for message in sender.received_messages
            if message.message_type == "rpc" and message.rpc_phase == "response"
        ]
        self.assertEqual(len(responses), 1)
        self.assertFalse(responses[0].ok)
        self.assertEqual(responses[0].payload["status"], "protocol_error")
        self.assertEqual(responses[0].payload["attempted_directive_kind"], "finish_run")

    async def test_custom_handler_still_supported(self) -> None:
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.register_rpc_handler("assistant.ping", lambda _message: {"reply": "pong"})
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_rpc_request(
            "assistant.ping",
            {},
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        responses = [
            message
            for message in sender.received_messages
            if message.message_type == "rpc" and message.rpc_phase == "response"
        ]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].payload["reply"], "pong")

    async def test_rpc_response_is_delivered_back_to_target_agent_for_follow_up_action(self) -> None:
        monitor = PassiveAgent(
            AgentInstance(id="monitor", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        requester = AssistantAgent(
            AgentInstance(id="requester", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        worker = AssistantAgent(
            AgentInstance(id="worker", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        worker.register_rpc_handler(
            "worker.echo",
            lambda _message: {
                "reply": "worker done",
                "request_overrides": {
                    "tool_name": "send_event",
                    "tool_arguments": {
                        "topic": "assistant.received",
                        "payload": {"status": "from rpc response"},
                        "target_agent_id": monitor.agent_id,
                    },
                },
            },
        )
        await self.center.register(monitor)
        await self.center.register(requester)
        await self.center.register(worker)

        await requester.send_rpc_request(
            "worker.echo",
            {"content": "delegate"},
            target_agent_id=worker.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        self.assertEqual(len(monitor.received_events), 1)
        self.assertEqual(monitor.received_events[0].topic, "assistant.received")
        tasks = await self.task_repository.list_by_run(self.run_id)
        self.assertEqual([task.source_message_kind for task in tasks], ["rpc_response"])
        self.assertEqual(tasks[0].agent_id, requester.agent_id)

    async def test_broadcast_subscriptions_respect_user_proxy_metadata(self) -> None:
        user_record = await self.agent_instance_repository.get_or_create_primary(
            self.session_id,
            "user_proxy",
            profile_id=None,
            display_name="UserProxy",
        )
        assistant_record = await self.agent_instance_repository.get_or_create_primary(
            self.session_id,
            "assistant",
            profile_id="assistant_default",
            display_name="Assistant",
        )
        runtime_agents = await self.roster_manager.hydrate_run_roster(self.session_id, self.run_id)
        user = await self.roster_manager.ensure_primary_user_proxy(self.session_id)
        assistant = runtime_agents[assistant_record.id]
        assert isinstance(user, UserProxyAgent)
        assert isinstance(assistant, AssistantAgent)

        delivered = await assistant.broadcast_event(
            "workflow.updated",
            {"status": "shared"},
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        self.assertEqual(delivered, 0)
        self.assertEqual(len(user.received_events), 0)

        user.instance = user.instance.model_copy(
            update={"metadata": {"subscribed_topics": ["workflow.updated"]}}
        )
        delivered = await assistant.broadcast_event(
            "workflow.updated",
            {"status": "shared-again"},
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        self.assertEqual(delivered, 1)
        self.assertEqual(len(user.received_events), 1)
        self.assertEqual(user.received_events[0].topic, "workflow.updated")
        self.assertEqual(user_record.id, user.agent_id)

    async def test_user_proxy_is_not_woken_by_unrelated_shared_messages(self) -> None:
        user = await self.roster_manager.ensure_primary_user_proxy(self.session_id)
        sender = PassiveAgent(
            AgentInstance(id="sender", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
        )
        assistant = AssistantAgent(
            AgentInstance(id="assistant", agent_type="assistant", role="assistant"),
            self.center,
            observer=self.observer,
            task_manager=self.task_manager,
        )
        assistant.register_rpc_handler("assistant.ping", lambda _message: {"reply": "pong"})
        await self.center.register(sender)
        await self.center.register(assistant)

        await sender.send_rpc_request(
            "assistant.ping",
            {},
            target_agent_id=assistant.agent_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )
        await self.center.drain()

        shared = await self.message_center_repository.list_latest_shared(self.session_id, limit=10)
        self.assertEqual(len(shared), 2)
        self.assertEqual(user.received_events, [])
        user_events = self.observer.list_events(agent_id=user.agent_id)
        self.assertEqual(user_events, [])
