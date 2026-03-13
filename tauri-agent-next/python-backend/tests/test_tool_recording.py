from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.execution.react_strategy import ReactStrategy
from agents.execution.directives import visible_directive_kinds_for_agent
from agents.execution.strategy import ExecutionContext
from agents.execution.tool_executor import ToolExecutor
from agents.execution.tool_recorder import PrivateExecutionRecorder
from agents.message import AgentMessage
from observation.center import ObservationCenter
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore
from tools.base import Tool, ToolParameter, ToolRegistry


class EchoTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "echo_record"
        self.description = "Return the supplied text."
        self.parameters = [
            ToolParameter(name="text", type="string", description="Text to echo")
        ]

    async def execute(self, arguments):
        return arguments["text"]


class ReservedFinishTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "finish_run"
        self.description = "A conflicting custom tool for testing."
        self.parameters = []

    async def execute(self, arguments):
        return {"unexpected": arguments}


class _FakeMemory:
    def _resolve_cfg(self, _value):
        return {}

    async def build_history_for_agent(self, *args, **kwargs):
        return []

    async def ensure_budget_for_view(self, prompt_ir, **kwargs):
        return prompt_ir


class _FakeSequencedLlmClient:
    def __init__(self, turns, *, api_format: str = "openai_chat_completions") -> None:
        self.config = SimpleNamespace(api_format=api_format)
        self._turns = [list(turn) for turn in turns]

    async def chat_stream_events(self, prompt_ir, request_overrides=None):
        turn = self._turns.pop(0) if self._turns else []
        for event in turn:
            yield event


class ToolRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repo = SessionRepository(self.store)
        self.shared_repo = SharedFactRepository(self.store)
        self.private_repo = AgentPrivateEventRepository(self.store)
        self.observation_center = ObservationCenter(
            shared_fact_repository=self.shared_repo,
            agent_private_event_repository=self.private_repo,
        )
        self.session_id = "session-1"
        await self.session_repo.create(session_id=self.session_id)
        ToolRegistry.clear()
        ToolRegistry.register(EchoTool())

    async def asyncTearDown(self) -> None:
        ToolRegistry.clear()
        self._temp_dir.cleanup()

    async def test_tool_calls_are_recorded_as_private_events(self) -> None:
        tool_executor = ToolExecutor(
            recorder=PrivateExecutionRecorder(
                self.observation_center,
                max_payload_bytes=64 * 1024,
            )
        )
        trigger_fact = await self.observation_center.append_shared_fact(
            session_id=self.session_id,
            run_id="run-1",
            sender_id="UserProxy",
            target_agent_id="assistant-1",
            topic="task.run",
            fact_type="rpc_request",
            payload_json={"content": "hello"},
        )
        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            work_path=None,
            metadata={"task_id": "task-1", "trigger_fact_id": trigger_fact.fact_id},
            tool_name="echo_record",
            arguments={"text": "hello"},
            tool_call_id="call-1",
        )
        self.assertTrue(result.ok)

        events = await self.private_repo.list(
            self.session_id,
            owner_agent_id="assistant-1",
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual([event.trigger_fact_id for event in events], [trigger_fact.fact_id] * 2)
        self.assertEqual([event.task_id for event in events], ["task-1", "task-1"])

    async def test_reserved_directive_tool_names_cannot_be_overridden(self) -> None:
        ToolRegistry.clear()
        ToolRegistry.register(ReservedFinishTool())
        tool_executor = ToolExecutor(
            allowed_builtin_tool_names=visible_directive_kinds_for_agent(
                agent_type="assistant",
                role=None,
            )
        )

        tool_names = {tool.name for tool in tool_executor.list_tools()}
        self.assertIn("handoff", tool_names)
        self.assertNotIn("send_event", tool_names)
        self.assertNotIn("finish_run", tool_names)

        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            work_path=None,
            metadata={},
            tool_name="finish_run",
            arguments={},
            tool_call_id="call-2",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Tool not found: finish_run")

    async def test_hidden_builtin_directive_can_execute_for_internal_override(self) -> None:
        tool_executor = ToolExecutor(
            allowed_builtin_tool_names=visible_directive_kinds_for_agent(
                agent_type="assistant",
                role=None,
            )
        )

        tool_names = {tool.name for tool in tool_executor.list_tools()}
        self.assertNotIn("send_event", tool_names)

        result = await tool_executor.execute(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-override",
            session_id=self.session_id,
            work_path=None,
            metadata={},
            tool_name="send_event",
            arguments={
                "topic": "run.controller.input",
                "payload": {"content": "relay"},
                "target_agent_id": "controller-1",
            },
            tool_call_id="call-hidden",
            allow_hidden_builtin_tools=True,
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.directive)
        assert result.directive is not None
        self.assertEqual(result.directive.kind, "send_event")

    async def test_handoff_tool_rejects_blank_target_or_instruction(self) -> None:
        tool_executor = ToolExecutor(
            allowed_builtin_tool_names=visible_directive_kinds_for_agent(
                agent_type="assistant",
                role=None,
            )
        )
        handoff_tool = tool_executor.get_tool("handoff")

        self.assertIsNotNone(handoff_tool)
        assert handoff_tool is not None
        self.assertFalse(handoff_tool.validate_input({"target_profile": "", "instruction": "go"}))
        self.assertFalse(
            handoff_tool.validate_input({"target_profile": "planner", "instruction": ""})
        )
        self.assertTrue(
            handoff_tool.validate_input({"target_profile": "planner", "instruction": "go"})
        )

    async def test_failed_tool_invocation_is_recorded_as_private_events(self) -> None:
        tool_executor = ToolExecutor(
            recorder=PrivateExecutionRecorder(
                self.observation_center,
                max_payload_bytes=64 * 1024,
            )
        )

        result = await tool_executor.record_failed_tool_invocation(
            agent_id="assistant-1",
            run_id="run-1",
            message_id="msg-1",
            session_id=self.session_id,
            metadata={"task_id": "task-parse"},
            tool_name="finish_run",
            arguments='{"reply":"done"}{"status":"completed"}',
            error="Invalid tool arguments JSON: extra data",
            tool_call_id="call-parse",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Invalid tool arguments JSON: extra data")

        events = await self.private_repo.list(
            self.session_id,
            owner_agent_id="assistant-1",
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual([event.tool_call_id for event in events], ["call-parse", "call-parse"])
        self.assertEqual(events[1].payload_json["error"], "Invalid tool arguments JSON: extra data")

    async def test_react_handles_duplicate_streamed_tool_call_chunks(self) -> None:
        tool_executor = ToolExecutor(
            recorder=PrivateExecutionRecorder(
                self.observation_center,
                max_payload_bytes=64 * 1024,
            )
        )
        strategy = ReactStrategy(max_iterations=2)
        llm_client = _FakeSequencedLlmClient(
            turns=[
                [
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "call-1",
                        "name": "echo_record",
                        "arguments": '{"text":"he',
                        "arguments_delta": '{"text":"he',
                    },
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "call-1",
                        "name": "echo_record",
                        "arguments": 'llo"}',
                        "arguments_delta": 'llo"}',
                    },
                    {"type": "done", "tool_calls": []},
                ],
                [
                    {"type": "content", "delta": "tool complete"},
                    {"type": "done", "tool_calls": []},
                ],
            ]
        )
        message = AgentMessage.build_event(
            topic="task.run",
            sender_id="external:http",
            target_id="assistant-1",
            payload={"content": "hello"},
            session_id=self.session_id,
            run_id="run-1",
        )

        steps = [
            step
            async for step in strategy.execute(
                message,
                agent_id="assistant-1",
                llm_client=llm_client,
                tool_executor=tool_executor,
                memory=_FakeMemory(),
                execution_context=ExecutionContext(system_prompt="test"),
            )
        ]

        self.assertEqual(steps[-1].step_type, "answer")
        self.assertEqual(steps[-1].content, "tool complete")
        self.assertTrue(
            any(
                step.step_type == "observation"
                and step.metadata.get("tool_name") == "echo_record"
                and step.content == "hello"
                for step in steps
            )
        )

        events = await self.private_repo.list(
            self.session_id,
            owner_agent_id="assistant-1",
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual(events[0].payload_json["arguments"], {"text": "hello"})

    async def test_react_parse_errors_are_recorded_as_private_events(self) -> None:
        tool_executor = ToolExecutor(
            recorder=PrivateExecutionRecorder(
                self.observation_center,
                max_payload_bytes=64 * 1024,
            )
        )
        strategy = ReactStrategy(max_iterations=1)
        llm_client = _FakeSequencedLlmClient(
            turns=[
                [
                    {
                        "type": "done",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-bad-json",
                                "name": "finish_run",
                                "arguments": '{"reply":"done"}{"status":"completed"}',
                            }
                        ],
                    }
                ]
            ]
        )
        message = AgentMessage.build_event(
            topic="task.run",
            sender_id="external:http",
            target_id="assistant-1",
            payload={"content": "hello"},
            session_id=self.session_id,
            run_id="run-1",
        )

        steps = [
            step
            async for step in strategy.execute(
                message,
                agent_id="assistant-1",
                llm_client=llm_client,
                tool_executor=tool_executor,
                memory=_FakeMemory(),
                execution_context=ExecutionContext(system_prompt="test"),
            )
        ]

        self.assertTrue(
            any(
                step.step_type == "observation"
                and "Invalid tool arguments JSON" in step.content
                for step in steps
            )
        )

        events = await self.private_repo.list(
            self.session_id,
            owner_agent_id="assistant-1",
            after_id=0,
            limit=10,
        )
        self.assertEqual([event.kind for event in events], ["tool_call", "tool_result"])
        self.assertEqual([event.tool_call_id for event in events], ["call-bad-json", "call-bad-json"])
        self.assertIn("Invalid tool arguments JSON", events[1].payload_json["error"])
