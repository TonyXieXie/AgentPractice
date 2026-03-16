import copy
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.base import AgentStep  # noqa: E402
from graph_runtime.runtime import GRAPH_END, GraphRunner  # noqa: E402
from models import ChatRequest, ChatSession, GraphNodeRun, GraphRun  # noqa: E402
from tools.base import Tool, ToolParameter  # noqa: E402
import graph_runtime.runtime as runtime_module  # noqa: E402


class EchoTool(Tool):
    def __init__(self, name: str = "echo", fail: bool = False) -> None:
        super().__init__()
        self.name = name
        self.description = "Echo text"
        self.parameters = [
            ToolParameter(name="text", type="string", description="Text to echo")
        ]
        self.fail = fail

    async def execute(self, input_data: str) -> str:
        if self.fail:
            raise RuntimeError("boom")
        return input_data


class FakeExecutor:
    def __init__(self, step_factory) -> None:
        self.step_factory = step_factory

    async def run(self, *, user_input, **kwargs):
        try:
            steps = self.step_factory(user_input, kwargs)
        except TypeError:
            steps = self.step_factory(user_input)
        for step in steps:
            yield step


class FakeReActAgent:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def _get_tool(self, tools, tool_name):
        for tool in tools:
            if getattr(tool, "name", None) == tool_name:
                return tool
        return None

    def _extract_tool_input(self, tool, args):
        if not tool.parameters:
            return ""
        key = tool.parameters[0].name
        value = args.get(key, "")
        return str(value)

    async def _execute_tool(self, tool, tool_input: str) -> str:
        try:
            result = await tool.execute(tool_input)
            return "" if result is None else str(result)
        except Exception as exc:
            return f"Tool execution failed: {exc}"

    async def _stream_run_shell_tool(self, *args, **kwargs):
        if False:
            yield None
        raise AssertionError("run_shell is not used in these tests")

    def _record_tool_call_history(self, **kwargs) -> None:
        return None


class FakeSessionRepository:
    def __init__(self, session: ChatSession) -> None:
        self.session = copy.deepcopy(session)
        self.graph_runs = {}
        self.graph_node_runs = {}

    def create_graph_run(self, graph_run: GraphRun) -> GraphRun:
        stored = copy.deepcopy(graph_run)
        stored.id = stored.id or f"graph-run-{len(self.graph_runs) + 1}"
        self.graph_runs[stored.id] = stored
        return copy.deepcopy(stored)

    def update_graph_run(
        self,
        graph_run_id: str,
        *,
        state_json=None,
        active_node_id=None,
        status=None,
        hop_count=None,
        last_result=None,
        error=None,
        completed_at=None,
    ) -> GraphRun:
        stored = self.graph_runs[graph_run_id]
        if state_json is not None:
            stored.state_json = copy.deepcopy(state_json)
        if active_node_id is not None:
            stored.active_node_id = active_node_id
        if status is not None:
            stored.status = status
        if hop_count is not None:
            stored.hop_count = int(hop_count)
        if last_result is not None:
            stored.last_result = copy.deepcopy(last_result)
        if error is not None:
            stored.error = copy.deepcopy(error)
        if completed_at is not None:
            stored.completed_at = completed_at
        return copy.deepcopy(stored)

    def create_graph_node_run(self, node_run: GraphNodeRun) -> GraphNodeRun:
        stored = copy.deepcopy(node_run)
        stored.id = stored.id or f"graph-node-run-{len(self.graph_node_runs) + 1}"
        self.graph_node_runs[stored.id] = stored
        return copy.deepcopy(stored)

    def update_graph_node_run(
        self,
        graph_node_run_id: str,
        *,
        status=None,
        output_json=None,
        state_patch_json=None,
        error_json=None,
        completed_at=None,
        duration_ms=None,
    ) -> GraphNodeRun:
        stored = self.graph_node_runs[graph_node_run_id]
        if status is not None:
            stored.status = status
        if output_json is not None:
            stored.output_json = copy.deepcopy(output_json)
        if state_patch_json is not None:
            stored.state_patch_json = copy.deepcopy(state_patch_json)
        if error_json is not None:
            stored.error_json = copy.deepcopy(error_json)
        if completed_at is not None:
            stored.completed_at = completed_at
        if duration_ms is not None:
            stored.duration_ms = duration_ms
        return copy.deepcopy(stored)

    def list_graph_node_runs(self, graph_run_id: str):
        runs = [
            copy.deepcopy(node_run)
            for node_run in self.graph_node_runs.values()
            if node_run.graph_run_id == graph_run_id
        ]
        return sorted(runs, key=lambda item: (item.sequence, item.started_at or ""))

    def update_session(self, session_id: str, update):
        if session_id != self.session.id:
            return None
        if getattr(update, "agent_profile", None) is not None:
            self.session.agent_profile = update.agent_profile
        return copy.deepcopy(self.session)

    def update_context_estimate(self, session_id: str, estimate):
        return None


class GraphRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = ChatSession(
            id="session-1",
            title="Test",
            config_id="config-1",
            agent_profile="default",
        )
        self.request = ChatRequest(message="hello", session_id=self.session.id)
        self.config = SimpleNamespace(reasoning_summary=None)
        self.base_app_config = {
            "llm": {"reasoning_summary": "detailed"},
            "agent": {
                "default_graph_id": "graph_under_test",
                "react_max_iterations": 5,
                "graphs": [],
            },
        }

    async def test_tool_router_react_graph_completes(self) -> None:
        tool = EchoTool()
        repo = FakeSessionRepository(self.session)
        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "Tool Router React",
                "initial_state": {},
                "nodes": [
                    {
                        "id": "load_value",
                        "type": "tool_call",
                        "tool_name": "echo",
                        "args_template": {"text": "42"},
                        "output_path": "tool.value",
                    },
                    {"id": "branch", "type": "router"},
                    {
                        "id": "answer",
                        "type": "react_agent",
                        "input_template": "value={{state.tool.value}}",
                        "output_path": "final.answer",
                    },
                ],
                "edges": [
                    {"id": "start_to_tool", "source": "__start__", "target": "load_value"},
                    {"id": "tool_to_router", "source": "load_value", "target": "branch"},
                    {"id": "router_to_react", "source": "branch", "target": "answer"},
                    {"id": "react_to_end", "source": "answer", "target": "__end__"},
                ],
            }
        ]

        with (
            mock.patch.object(runtime_module, "session_repository", repo),
            mock.patch.object(runtime_module, "ReActAgent", FakeReActAgent),
            mock.patch.object(runtime_module.ToolRegistry, "get_all", return_value=[tool]),
            mock.patch.object(runtime_module, "build_agent_prompt_and_tools", return_value=("system", [], None, [])),
            mock.patch.object(runtime_module, "append_reasoning_summary_prompt", side_effect=lambda prompt, summary: prompt),
            mock.patch.object(
                runtime_module,
                "create_agent_executor",
                side_effect=lambda **kwargs: FakeExecutor(
                    lambda user_input: [
                        AgentStep(step_type="answer", content=f"react:{user_input}", metadata={})
                    ]
                ),
            ),
        ):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=1,
                assistant_message_id=2,
                user_input="hello",
                history=[],
                request_overrides={},
            )
            steps = [step async for step in runner.run()]

        self.assertEqual([step.step_type for step in steps], ["action", "observation", "observation", "answer"])
        self.assertEqual(steps[-1].content, "react:value=42")
        self.assertEqual(steps[-1].metadata["node_id"], "answer")
        self.assertEqual(steps[-1].metadata["node_type"], "react_agent")

        graph_run = next(iter(repo.graph_runs.values()))
        self.assertEqual(graph_run.status, "completed")
        self.assertEqual(graph_run.active_node_id, GRAPH_END)
        self.assertEqual(graph_run.state_json["tool"]["value"], "42")
        self.assertEqual(graph_run.state_json["final"]["answer"], "react:value=42")

    async def test_graph_runtime_injects_state_input_and_messages(self) -> None:
        repo = FakeSessionRepository(self.session)
        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "State First",
                "initial_state": {"plan": {"current_task": None}},
                "state_schema": [{"path": "plan.current_task", "type": "string", "mutable": True}],
                "nodes": [
                    {
                        "id": "answer",
                        "type": "react_agent",
                        "profile_id": "planner",
                    },
                ],
                "edges": [
                    {"id": "start_to_answer", "source": "__start__", "target": "answer"},
                    {"id": "answer_to_end", "source": "answer", "target": "__end__"},
                ],
            }
        ]

        received_inputs = []
        received_overrides = []

        with (
            mock.patch.object(runtime_module, "session_repository", repo),
            mock.patch.object(runtime_module, "build_agent_prompt_and_tools", return_value=("system", [], "planner", [])),
            mock.patch.object(runtime_module, "append_reasoning_summary_prompt", side_effect=lambda prompt, summary: prompt),
            mock.patch.object(
                runtime_module,
                "create_agent_executor",
                side_effect=lambda **kwargs: FakeExecutor(
                    lambda user_input, run_kwargs: (
                        received_inputs.append(user_input),
                        received_overrides.append(dict(run_kwargs.get("request_overrides", {}))),
                        [AgentStep(step_type="answer", content="state-first-ok", metadata={})],
                    )[-1]
                ),
            ),
        ):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=11,
                assistant_message_id=12,
                user_input="hello",
                history=[],
                request_overrides={"user_content": [{"type": "text", "text": "hello"}], "_post_user_messages": [{"role": "user", "content": "extra"}]},
                user_content=[{"type": "text", "text": "hello"}],
            )
            steps = [step async for step in runner.run()]

        self.assertEqual(steps[-1].content, "state-first-ok")
        self.assertEqual(received_inputs, [runtime_module._DEFAULT_GRAPH_NODE_INPUT])
        self.assertNotIn("user_content", received_overrides[0])
        self.assertNotIn("_post_user_messages", received_overrides[0])

        graph_run = next(iter(repo.graph_runs.values()))
        self.assertEqual(graph_run.state_json["input"]["user_message"], "hello")
        self.assertEqual(graph_run.state_json["input"]["user_content"], [{"type": "text", "text": "hello"}])
        self.assertEqual(graph_run.state_json["messages"][0]["role"], "user")
        self.assertEqual(graph_run.state_json["messages"][0]["content"], "hello")
        self.assertEqual(graph_run.state_json["messages"][-1]["author"], "planner")
        self.assertEqual(graph_run.state_json["messages"][-1]["content"], "state-first-ok")

    async def test_graph_state_updates_are_visible_and_persisted(self) -> None:
        repo = FakeSessionRepository(self.session)
        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "Graph State Tool",
                "initial_state": {"plan": {"current_task": None}},
                "state_schema": [{"path": "plan.current_task", "type": "string", "mutable": True}],
                "nodes": [
                    {
                        "id": "answer",
                        "type": "react_agent",
                        "profile_id": "coder",
                    },
                ],
                "edges": [
                    {"id": "start_to_answer", "source": "__start__", "target": "answer"},
                    {"id": "answer_to_end", "source": "answer", "target": "__end__"},
                ],
            }
        ]

        def step_factory(user_input, run_kwargs):
            graph_state_context = run_kwargs["request_overrides"]["_graph_state_context"]
            graph_state_context["set_state"]("plan.current_task", "implemented")
            self.assertEqual(graph_state_context["get_state"]()["plan"]["current_task"], "implemented")
            return [AgentStep(step_type="answer", content="updated-state", metadata={})]

        with (
            mock.patch.object(runtime_module, "session_repository", repo),
            mock.patch.object(runtime_module, "build_agent_prompt_and_tools", return_value=("system", [], "coder", [])),
            mock.patch.object(runtime_module, "append_reasoning_summary_prompt", side_effect=lambda prompt, summary: prompt),
            mock.patch.object(
                runtime_module,
                "create_agent_executor",
                side_effect=lambda **kwargs: FakeExecutor(step_factory),
            ),
        ):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=21,
                assistant_message_id=22,
                user_input="hello",
                history=[],
                request_overrides={},
            )
            steps = [step async for step in runner.run()]

        self.assertEqual(steps[-1].content, "updated-state")
        graph_run = next(iter(repo.graph_runs.values()))
        self.assertEqual(graph_run.state_json["plan"]["current_task"], "implemented")
        self.assertEqual(graph_run.state_json["messages"][-1]["author"], "coder")

        node_run = next(iter(repo.graph_node_runs.values()))
        self.assertEqual(node_run.state_patch_json, {"plan": {"current_task": "implemented"}})
        self.assertEqual(node_run.output_json["output"], "updated-state")

    async def test_error_edge_routes_to_recovery_node(self) -> None:
        tool = EchoTool(fail=True)
        repo = FakeSessionRepository(self.session)
        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "Error Routing",
                "initial_state": {},
                "nodes": [
                    {
                        "id": "danger_tool",
                        "type": "tool_call",
                        "tool_name": "echo",
                        "args_template": {"text": "payload"},
                        "output_path": "tool.last",
                    },
                    {
                        "id": "recover",
                        "type": "react_agent",
                        "input_template": "recovered={{state.tool.last}}",
                        "output_path": "final.answer",
                    },
                    {
                        "id": "unexpected",
                        "type": "react_agent",
                        "input_template": "unexpected",
                        "output_path": "final.answer",
                    },
                ],
                "edges": [
                    {"id": "start_to_tool", "source": "__start__", "target": "danger_tool"},
                    {
                        "id": "tool_error_to_recover",
                        "source": "danger_tool",
                        "target": "recover",
                        "condition": "result.status == 'error'",
                    },
                    {
                        "id": "tool_success_to_unexpected",
                        "source": "danger_tool",
                        "target": "unexpected",
                    },
                    {"id": "recover_to_end", "source": "recover", "target": "__end__"},
                    {"id": "unexpected_to_end", "source": "unexpected", "target": "__end__"},
                ],
            }
        ]

        with (
            mock.patch.object(runtime_module, "session_repository", repo),
            mock.patch.object(runtime_module, "ReActAgent", FakeReActAgent),
            mock.patch.object(runtime_module.ToolRegistry, "get_all", return_value=[tool]),
            mock.patch.object(runtime_module, "build_agent_prompt_and_tools", return_value=("system", [], None, [])),
            mock.patch.object(runtime_module, "append_reasoning_summary_prompt", side_effect=lambda prompt, summary: prompt),
            mock.patch.object(
                runtime_module,
                "create_agent_executor",
                side_effect=lambda **kwargs: FakeExecutor(
                    lambda user_input: [
                        AgentStep(step_type="answer", content=f"handled:{user_input}", metadata={})
                    ]
                ),
            ),
        ):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=10,
                assistant_message_id=20,
                user_input="hello",
                history=[],
                request_overrides={},
            )
            steps = [step async for step in runner.run()]

        self.assertEqual(steps[-1].content, "handled:recovered=Tool execution failed: boom")
        self.assertEqual(steps[-1].metadata["edge_id"], "tool_error_to_recover")
        self.assertEqual(steps[-1].metadata["node_id"], "recover")

    async def test_loop_graph_stops_at_max_hops(self) -> None:
        repo = FakeSessionRepository(self.session)
        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "Loop",
                "initial_state": {},
                "max_hops": 2,
                "nodes": [
                    {"id": "loop", "type": "router"},
                ],
                "edges": [
                    {"id": "start_to_loop", "source": "__start__", "target": "loop"},
                    {"id": "loop_again", "source": "loop", "target": "loop", "condition": "true"},
                    {"id": "loop_to_end", "source": "loop", "target": "__end__"},
                ],
            }
        ]

        with mock.patch.object(runtime_module, "session_repository", repo):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=100,
                assistant_message_id=200,
                user_input="hello",
                history=[],
                request_overrides={},
            )
            steps = [step async for step in runner.run()]

        self.assertEqual(steps[-1].step_type, "error")
        self.assertIn("max_hops=2", steps[-1].content)
        graph_run = next(iter(repo.graph_runs.values()))
        self.assertEqual(graph_run.status, "failed")
        self.assertEqual(graph_run.hop_count, 2)

    async def test_resume_infers_incoming_edge_metadata(self) -> None:
        repo = FakeSessionRepository(self.session)
        existing_graph_run = GraphRun(
            id="resume-run",
            session_id=self.session.id,
            user_message_id=7,
            assistant_message_id=8,
            graph_id="graph_under_test",
            request_text="hello",
            state_json={"tool": {"value": "42"}},
            active_node_id="answer",
            status="running",
            hop_count=1,
            last_result={
                "status": "completed",
                "output": "42",
                "state_patch": {},
                "steps": [],
                "error": None,
            },
        )
        repo.graph_runs[existing_graph_run.id] = copy.deepcopy(existing_graph_run)
        repo.graph_node_runs["resume-node-1"] = GraphNodeRun(
            id="resume-node-1",
            graph_run_id=existing_graph_run.id,
            node_id="load_value",
            node_type="tool_call",
            sequence=1,
            status="completed",
            input_json={},
            output_json={"output": "42"},
            state_patch_json={},
            error_json=None,
        )

        app_config = copy.deepcopy(self.base_app_config)
        app_config["agent"]["graphs"] = [
            {
                "id": "graph_under_test",
                "name": "Resume",
                "initial_state": {},
                "nodes": [
                    {
                        "id": "load_value",
                        "type": "tool_call",
                        "tool_name": "echo",
                        "args_template": {"text": "42"},
                        "output_path": "tool.value",
                    },
                    {
                        "id": "answer",
                        "type": "react_agent",
                        "input_template": "value={{state.tool.value}}",
                        "output_path": "final.answer",
                    },
                ],
                "edges": [
                    {"id": "start_to_tool", "source": "__start__", "target": "load_value"},
                    {"id": "tool_to_answer", "source": "load_value", "target": "answer"},
                    {"id": "answer_to_end", "source": "answer", "target": "__end__"},
                ],
            }
        ]

        with (
            mock.patch.object(runtime_module, "session_repository", repo),
            mock.patch.object(runtime_module, "build_agent_prompt_and_tools", return_value=("system", [], None, [])),
            mock.patch.object(runtime_module, "append_reasoning_summary_prompt", side_effect=lambda prompt, summary: prompt),
            mock.patch.object(
                runtime_module,
                "create_agent_executor",
                side_effect=lambda **kwargs: FakeExecutor(
                    lambda user_input: [
                        AgentStep(step_type="answer", content=f"resume:{user_input}", metadata={})
                    ]
                ),
            ),
        ):
            runner = GraphRunner(
                app_config=app_config,
                session=self.session,
                request=self.request,
                config=self.config,
                llm_client=object(),
                graph_id="graph_under_test",
                user_message_id=7,
                assistant_message_id=8,
                user_input="hello",
                history=[],
                request_overrides={},
                existing_graph_run=existing_graph_run,
            )
            steps = [step async for step in runner.run()]

        self.assertEqual(steps[-1].content, "resume:value=42")
        self.assertEqual(steps[-1].metadata["edge_id"], "tool_to_answer")
        updated_run = repo.graph_runs[existing_graph_run.id]
        self.assertEqual(updated_run.status, "completed")
        self.assertEqual(updated_run.active_node_id, GRAPH_END)


if __name__ == "__main__":
    unittest.main()
