import json
import re
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from agents.base import AgentStep
from agents.executor import create_agent_executor
from agents.prompt_builder import build_agent_prompt_and_tools
from agents.react import ReActAgent
from models import ChatRequest, ChatSessionUpdate, GraphNodeRun, GraphRun, NodeResult
from repositories import session_repository
from server.agent_prompt_support import append_reasoning_summary_prompt, build_live_pty_prompt
from tools.base import ToolRegistry
from tools.builtin.graph_state_tool import GraphStateTool

from .expression import evaluate_edge_expression


GRAPH_START = "__start__"
GRAPH_END = "__end__"
_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
_RESERVED_STATE_ROOTS = {"input", "messages"}
_DEFAULT_GRAPH_NODE_INPUT_TEMPLATE = "{{state.input.user_message}}"


def get_graph_definition(app_config: Dict[str, Any], graph_id: Optional[str]) -> Dict[str, Any]:
    agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    graphs = agent_config.get("graphs") if isinstance(agent_config, dict) else []
    default_graph_id = agent_config.get("default_graph_id") if isinstance(agent_config, dict) else None
    resolved_graph_id = graph_id or default_graph_id
    if not isinstance(graphs, list) or not graphs:
        raise ValueError("No graphs configured")
    for graph in graphs:
        if isinstance(graph, dict) and graph.get("id") == resolved_graph_id:
            return deepcopy(graph)
    raise ValueError(f"Unknown graph_id: {resolved_graph_id}")


def get_graph_initial_state(app_config: Dict[str, Any], graph: Dict[str, Any]) -> Any:
    agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    preset_id = graph.get("state_preset_id") if isinstance(graph, dict) else None
    if isinstance(agent_config, dict) and isinstance(preset_id, str) and preset_id.strip():
        state_presets = agent_config.get("state_presets")
        if isinstance(state_presets, list):
            for preset in state_presets:
                if isinstance(preset, dict) and preset.get("id") == preset_id:
                    return deepcopy(preset.get("state", {}))
    return deepcopy(graph.get("initial_state", {}))


def resolve_graph_id(
    app_config: Dict[str, Any],
    requested_graph_id: Optional[str],
    session_graph_id: Optional[str],
) -> str:
    graph = get_graph_definition(
        app_config,
        requested_graph_id or session_graph_id or app_config.get("agent", {}).get("default_graph_id"),
    )
    return str(graph.get("id"))


def _resolve_path(root: Any, path: str) -> Any:
    current = root
    for part in [segment for segment in path.split(".") if segment]:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        return None
    return current


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_template(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        full_match = _TEMPLATE_PATTERN.fullmatch(value.strip())
        if full_match:
            return _resolve_path(context, full_match.group(1))

        def replacer(match: re.Match) -> str:
            resolved = _resolve_path(context, match.group(1))
            return _stringify_template_value(resolved)

        return _TEMPLATE_PATTERN.sub(replacer, value)
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    return deepcopy(value)


def _merge_state(base: Any, patch: Dict[str, Any]) -> Any:
    if not isinstance(patch, dict):
        return deepcopy(patch)
    if not isinstance(base, dict):
        base = {}
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_state(merged.get(key), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _ensure_container(current: Any, next_part: Optional[str]) -> Any:
    if isinstance(current, (dict, list)):
        return current
    if next_part is not None and next_part.isdigit():
        return []
    return {}


def _set_state_path(root: Any, path: Optional[str], value: Any) -> Any:
    if not path:
        return root
    parts = [segment for segment in path.split(".") if segment]
    if not parts:
        return root
    root_value = deepcopy(root)
    root_value = _ensure_container(root_value, parts[0])
    current = root_value
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        next_part = None if is_last else parts[index + 1]
        if isinstance(current, dict):
            if is_last:
                current[part] = deepcopy(value)
                return root_value
            next_value = _ensure_container(current.get(part), next_part)
            current[part] = next_value
            current = next_value
            continue
        if isinstance(current, list):
            try:
                list_index = int(part)
            except (TypeError, ValueError):
                return root_value
            while len(current) <= list_index:
                current.append({} if not (next_part and next_part.isdigit()) else [])
            if is_last:
                current[list_index] = deepcopy(value)
                return root_value
            next_value = _ensure_container(current[list_index], next_part)
            current[list_index] = next_value
            current = next_value
            continue
        return root_value
    return root_value


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


def _node_result_to_dict(node_result: NodeResult) -> Dict[str, Any]:
    return {
        "status": node_result.status,
        "output": node_result.output,
        "state_patch": node_result.state_patch,
        "steps": node_result.steps,
        "error": node_result.error,
    }


def _state_path_root(path: str) -> str:
    return str(path).split(".", 1)[0]


def _is_reserved_state_path(path: Optional[str]) -> bool:
    if not path:
        return False
    return _state_path_root(str(path)) in _RESERVED_STATE_ROOTS


def _get_effective_state_schema(app_config: Dict[str, Any], graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    graph_schema = graph.get("state_schema") if isinstance(graph, dict) else []
    preset_id = graph.get("state_preset_id") if isinstance(graph, dict) else None
    if isinstance(agent_config, dict) and isinstance(preset_id, str) and preset_id.strip():
        state_presets = agent_config.get("state_presets")
        if isinstance(state_presets, list):
            for preset in state_presets:
                if not isinstance(preset, dict) or preset.get("id") != preset_id:
                    continue
                preset_schema = preset.get("state_schema")
                if isinstance(preset_schema, list) and preset_schema:
                    return deepcopy(preset_schema)
                break
    return deepcopy(graph_schema) if isinstance(graph_schema, list) else []


def _append_state_message(state: Any, message: Dict[str, Any]) -> Any:
    current_messages = _resolve_path(state, "messages")
    if isinstance(current_messages, list):
        next_messages = deepcopy(current_messages)
    else:
        next_messages = []
    next_messages.append(deepcopy(message))
    return _set_state_path(state, "messages", next_messages)


def _inject_runtime_state(
    state: Any,
    *,
    user_input: str,
    attachments: Optional[Any],
    user_content: Optional[Any],
) -> Any:
    next_state = deepcopy(state) if isinstance(state, dict) else {}
    next_state = _set_state_path(next_state, "input.user_message", user_input)
    if attachments is not None:
        next_state = _set_state_path(next_state, "input.attachments", deepcopy(attachments))
    if user_content is not None:
        next_state = _set_state_path(next_state, "input.user_content", deepcopy(user_content))
    return _set_state_path(next_state, "messages", [])


class GraphRunner:
    def __init__(
        self,
        *,
        app_config: Dict[str, Any],
        session: Any,
        request: ChatRequest,
        config: Any,
        llm_client: Any,
        graph_id: str,
        user_message_id: int,
        assistant_message_id: int,
        user_input: str,
        history: List[Dict[str, Any]],
        request_overrides: Dict[str, Any],
        code_map_prompt: Optional[str] = None,
        user_content: Optional[Any] = None,
        existing_graph_run: Optional[GraphRun] = None,
    ) -> None:
        self.app_config = app_config
        self.session = session
        self.request = request
        self.config = config
        self.llm_client = llm_client
        self.graph = get_graph_definition(app_config, graph_id)
        self.graph_id = str(self.graph.get("id"))
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self.user_input = user_input
        self.user_content = user_content
        self.user_attachments = deepcopy(getattr(request, "attachments", None))
        self.history = history
        self.request_overrides = dict(request_overrides or {})
        self.code_map_prompt = code_map_prompt
        self.agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        self.global_reasoning_summary = (
            app_config.get("llm", {}).get("reasoning_summary")
            if isinstance(app_config.get("llm"), dict)
            else None
        )
        self.default_iterations = int(self.agent_config.get("react_max_iterations", 50) or 50)
        self.nodes = {
            node["id"]: deepcopy(node)
            for node in self.graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        self.state_schema = _get_effective_state_schema(self.app_config, self.graph)
        self.edges_by_source: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
        for order, edge in enumerate(self.graph.get("edges", []) or []):
            if not isinstance(edge, dict) or not edge.get("source"):
                continue
            self.edges_by_source.setdefault(str(edge["source"]), []).append((order, deepcopy(edge)))
        self.graph_run = existing_graph_run
        if existing_graph_run:
            self.state = deepcopy(existing_graph_run.state_json)
            self.active_node_id = existing_graph_run.active_node_id or GRAPH_START
            self.hop_count = int(existing_graph_run.hop_count or 0)
            self.last_result = deepcopy(existing_graph_run.last_result) if existing_graph_run.last_result else None
        else:
            self.state = _inject_runtime_state(
                get_graph_initial_state(self.app_config, self.graph),
                user_input=self.user_input,
                attachments=self.user_attachments,
                user_content=self.user_content,
            )
            self.active_node_id = GRAPH_START
            self.hop_count = 0
            self.last_result = None
        self.incoming_edge_id: Optional[str] = None
        self.final_answer: Optional[str] = None
        self.last_node_id: Optional[str] = None
        self.last_node_type: Optional[str] = None
        self._active_node_state_patch: Dict[str, Any] = {}
        self._active_node_profile_id: Optional[str] = None
        if existing_graph_run is not None:
            self.incoming_edge_id = self._infer_resume_edge_id()

    def _infer_resume_edge_id(self) -> Optional[str]:
        if not self.active_node_id or self.active_node_id == GRAPH_START:
            return None
        try:
            if int(self.hop_count or 0) <= 0:
                edge = self._select_edge(GRAPH_START, self.last_result)
                return edge.get("id") if str(edge.get("target")) == str(self.active_node_id) else None
            if self.graph_run is None or not self.graph_run.id:
                return None
            node_runs = session_repository.list_graph_node_runs(self.graph_run.id)
            previous_runs = [
                node_run
                for node_run in node_runs
                if int(getattr(node_run, "sequence", 0) or 0) == int(self.hop_count or 0)
            ]
            if not previous_runs:
                return None
            previous_run = previous_runs[-1]
            edge = self._select_edge(str(previous_run.node_id), self.last_result)
            return edge.get("id") if str(edge.get("target")) == str(self.active_node_id) else None
        except Exception:
            return None

    async def _initialize_graph_run(self) -> None:
        if self.graph_run is not None:
            return
        state_json = deepcopy(self.state)
        self.graph_run = session_repository.create_graph_run(
            GraphRun(
                session_id=self.session.id,
                user_message_id=self.user_message_id,
                assistant_message_id=self.assistant_message_id,
                graph_id=self.graph_id,
                request_text=self.user_input,
                state_json=state_json,
                active_node_id=GRAPH_START,
                status="running",
                hop_count=0,
            )
        )

    def _build_template_context(self) -> Dict[str, Any]:
        session_payload = _model_to_dict(self.session)
        return {
            "state": self.state,
            "result": self.last_result,
            "session": session_payload,
        }

    def _build_graph_system_prompt(self) -> str:
        mutable_paths = [
            str(field.get("path"))
            for field in self.state_schema
            if isinstance(field, dict) and field.get("path") and bool(field.get("mutable", False))
        ]
        mutable_summary = ", ".join(mutable_paths) if mutable_paths else "(no mutable fields configured)"
        return (
            "## State Context\n"
            "- The current request is stored at `state.input.user_message`.\n"
            "- Additional multimodal context may be available at `state.input.attachments` and `state.input.user_content`.\n"
            "- Treat `state` as the source of current-turn context.\n"
            "- Use the `graph_state` tool to inspect state and to update only fields marked mutable.\n"
            f"- Mutable state fields: {mutable_summary}\n"
            "- `state.input.*` and `state.messages` are read-only.\n"
            "- `state.messages` stores prior assistant summaries in order and is maintained automatically."
        )

    def _build_react_node_input(self, node: Dict[str, Any]) -> str:
        template_context = self._build_template_context()
        raw_template = node.get("input_template")
        if raw_template is None or (isinstance(raw_template, str) and not raw_template.strip()):
            raw_template = _DEFAULT_GRAPH_NODE_INPUT_TEMPLATE
        rendered_input = render_template(raw_template, template_context)
        node_input = _value_to_text(rendered_input).strip()
        if node_input:
            return node_input
        fallback_input = render_template(_DEFAULT_GRAPH_NODE_INPUT_TEMPLATE, template_context)
        return _value_to_text(fallback_input).strip()

    def _build_react_history(self) -> List[Dict[str, str]]:
        state_messages = _resolve_path(self.state, "messages")
        if not isinstance(state_messages, list):
            return []

        history: List[Dict[str, str]] = []
        for item in state_messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").lower() != "assistant":
                continue
            content = _value_to_text(item.get("content")).strip()
            if not content:
                continue
            author = _value_to_text(
                item.get("author") or item.get("profile_id") or item.get("node_id") or "assistant"
            ).strip()
            history.append(
                {
                    "role": "assistant",
                    "content": f"[{author}] {content}" if author else content,
                    "_after_user": True,
                }
            )
        return history

    def _set_graph_state_path(self, path: str, value: Any) -> None:
        self.state = _set_state_path(self.state, path, value)
        self._active_node_state_patch = _set_state_path(self._active_node_state_patch, path, value)

    def _build_graph_state_context(self, node: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "graph_run_id": self.graph_run.id if self.graph_run else None,
            "session_id": getattr(self.session, "id", None),
            "node_id": node.get("id"),
            "schema": deepcopy(self.state_schema),
            "get_state": lambda: deepcopy(self.state),
            "set_state": self._set_graph_state_path,
        }

    def _append_react_node_message(self, node: Dict[str, Any], node_result: NodeResult) -> None:
        content = _value_to_text(node_result.output).strip()
        if not content and isinstance(node_result.error, dict):
            content = _value_to_text(
                node_result.error.get("message")
                or node_result.error.get("content")
                or node_result.error
            ).strip()
        author = _value_to_text(node.get("name") or self._active_node_profile_id or node.get("id")).strip()
        message = {
            "kind": "message",
            "role": "assistant",
            "author": author,
            "node_id": node.get("id"),
            "profile_id": self._active_node_profile_id,
            "content": content,
            "status": node_result.status,
            "timestamp": datetime.now().isoformat(),
        }
        self.state = _append_state_message(self.state, message)

    def _select_edge(self, source: str, result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        candidates = sorted(
            self.edges_by_source.get(source, []),
            key=lambda item: (int(item[1].get("priority", 0) or 0), item[0]),
        )
        expression_context = {"state": self.state, "result": result}
        for _, edge in candidates:
            condition = edge.get("condition")
            if not condition:
                return edge
            if evaluate_edge_expression(str(condition), expression_context):
                return edge
        raise RuntimeError(f"No outgoing edge matched for node '{source}'")

    def _enrich_step(
        self,
        step: AgentStep,
        *,
        node_id: str,
        node_type: str,
        node_status: str,
        edge_id: Optional[str],
    ) -> AgentStep:
        metadata = dict(step.metadata or {})
        node_name = str(self.nodes.get(str(node_id), {}).get("name") or "").strip()
        metadata.update(
            {
                "graph_run_id": self.graph_run.id if self.graph_run else None,
                "graph_id": self.graph_id,
                "node_id": node_id,
                "node_name": node_name or node_id,
                "node_type": node_type,
                "node_status": node_status,
                "profile_id": self._active_node_profile_id,
            }
        )
        if edge_id:
            metadata["edge_id"] = edge_id
        return AgentStep(step_type=step.step_type, content=step.content, metadata=metadata)

    async def _execute_react_node(self, node: Dict[str, Any], sequence: int) -> AsyncGenerator[Any, None]:
        node_input = self._build_react_node_input(node)

        requested_profile = node.get("profile_id") or self.request.agent_profile or getattr(self.session, "agent_profile", None)
        pty_prompt = build_live_pty_prompt(self.session.id)
        system_prompt, tools, resolved_profile_id, ability_ids = build_agent_prompt_and_tools(
            requested_profile,
            ToolRegistry.get_all(),
            include_tools=True,
            extra_context={"pty_sessions": pty_prompt},
            exclude_ability_ids=["code_map"],
        )
        system_prompt = append_reasoning_summary_prompt(system_prompt, self.global_reasoning_summary)
        system_prompt = f"{system_prompt}\n\n{self._build_graph_system_prompt()}".strip()
        tools = [tool for tool in tools if getattr(tool, "name", None) != "graph_state"]
        tools.append(GraphStateTool())
        self._active_node_profile_id = resolved_profile_id

        if node.get("profile_id") is None and resolved_profile_id and resolved_profile_id != getattr(self.session, "agent_profile", None):
            updated_session = session_repository.update_session(
                self.session.id,
                ChatSessionUpdate(agent_profile=resolved_profile_id),
            )
            if updated_session is not None:
                self.session = updated_session

        request_overrides = dict(self.request_overrides)
        debug_ctx = dict(request_overrides.get("_debug") or {})
        debug_ctx["agent_type"] = "react"
        debug_ctx["graph_run_id"] = self.graph_run.id if self.graph_run else None
        debug_ctx["graph_id"] = self.graph_id
        debug_ctx["node_id"] = node.get("id")
        debug_ctx["profile_id"] = resolved_profile_id
        request_overrides["_debug"] = debug_ctx
        request_overrides.pop("user_content", None)
        request_overrides.pop("_post_user_messages", None)
        request_overrides["_graph_history_after_user"] = True
        request_overrides["_graph_state_context"] = self._build_graph_state_context(node)
        if self.code_map_prompt and "code_map" in ability_ids:
            request_overrides["_code_map_prompt"] = self.code_map_prompt
        else:
            request_overrides.pop("_code_map_prompt", None)

        executor = create_agent_executor(
            agent_type="react",
            llm_client=self.llm_client,
            tools=tools,
            max_iterations=int(node.get("max_iterations") or self.default_iterations or 50),
            system_prompt=system_prompt,
        )

        steps: List[Dict[str, Any]] = []
        final_output: Optional[str] = None
        error_payload: Optional[Dict[str, Any]] = None

        async for raw_step in executor.run(
            user_input=node_input,
            history=self._build_react_history(),
            session_id=self.session.id,
            request_overrides=request_overrides,
        ):
            if raw_step.step_type == "context_estimate":
                try:
                    session_repository.update_context_estimate(self.session.id, raw_step.metadata)
                except Exception:
                    pass

            if raw_step.step_type == "answer":
                final_output = raw_step.content
            elif raw_step.step_type == "error":
                if final_output is None:
                    final_output = raw_step.content
                error_payload = dict(raw_step.metadata or {})
                error_payload.setdefault("content", raw_step.content)
            elif not raw_step.step_type.endswith("_delta"):
                final_output = raw_step.content

            enriched = self._enrich_step(
                raw_step,
                node_id=node["id"],
                node_type=node["type"],
                node_status="running",
                edge_id=self.incoming_edge_id,
            )
            steps.append(enriched.to_dict())
            yield enriched

        has_answer = any(isinstance(step, dict) and step.get("step_type") == "answer" for step in steps)
        status = "error" if error_payload is not None and not has_answer else "completed"

        yield NodeResult(
            status=status,
            output=final_output,
            state_patch=deepcopy(self._active_node_state_patch),
            steps=steps,
            error=error_payload,
        )

    async def _execute_tool_call_node(self, node: Dict[str, Any], sequence: int) -> AsyncGenerator[Any, None]:
        helper = ReActAgent(max_iterations=1)
        tool_name = str(node.get("tool_name") or "").strip()
        tool = helper._get_tool(ToolRegistry.get_all(), tool_name)
        steps: List[Dict[str, Any]] = []

        if tool is None:
            error_step = self._enrich_step(
                AgentStep(step_type="error", content=f"Tool not found: '{tool_name}'", metadata={"tool": tool_name}),
                node_id=node["id"],
                node_type=node["type"],
                node_status="error",
                edge_id=self.incoming_edge_id,
            )
            steps.append(error_step.to_dict())
            yield error_step
            yield NodeResult(
                status="error",
                output=error_step.content,
                state_patch={},
                steps=steps,
                error={"tool": tool_name, "message": error_step.content},
            )
            return

        rendered_args = render_template(node.get("args_template", {}), self._build_template_context())
        if isinstance(rendered_args, dict):
            args_dict = rendered_args
        elif len(tool.parameters) == 1:
            args_dict = {tool.parameters[0].name: rendered_args}
        else:
            error_text = "Tool arguments must resolve to a JSON object for multi-parameter tools."
            error_step = self._enrich_step(
                AgentStep(step_type="error", content=error_text, metadata={"tool": tool_name}),
                node_id=node["id"],
                node_type=node["type"],
                node_status="error",
                edge_id=self.incoming_edge_id,
            )
            steps.append(error_step.to_dict())
            yield error_step
            yield NodeResult(
                status="error",
                output=error_text,
                state_patch={},
                steps=steps,
                error={"tool": tool_name, "message": error_text},
            )
            return

        tool_input = helper._extract_tool_input(tool, args_dict or {})
        action_step = self._enrich_step(
            AgentStep(
                step_type="action",
                content=tool_input,
                metadata={"tool": tool_name, "input": tool_input, "iteration": sequence},
            ),
            node_id=node["id"],
            node_type=node["type"],
            node_status="running",
            edge_id=self.incoming_edge_id,
        )
        steps.append(action_step.to_dict())
        yield action_step

        output_holder: Dict[str, str] = {}
        if tool_name.lower() == "run_shell":
            async for step in helper._stream_run_shell_tool(
                tool,
                tool_input,
                tool_name,
                sequence,
                f"graph-{self.graph_run.id}-{sequence}",
                output_holder,
                stop_event=self.request_overrides.get("_stop_event"),
            ):
                enriched = self._enrich_step(
                    step,
                    node_id=node["id"],
                    node_type=node["type"],
                    node_status="running",
                    edge_id=self.incoming_edge_id,
                )
                steps.append(enriched.to_dict())
                yield enriched
            tool_output = output_holder.get("output", "")
        else:
            tool_output = await helper._execute_tool(tool, tool_input)
            observation_step = self._enrich_step(
                AgentStep(
                    step_type="observation",
                    content=tool_output,
                    metadata={"tool": tool_name, "iteration": sequence},
                ),
                node_id=node["id"],
                node_type=node["type"],
                node_status="running",
                edge_id=self.incoming_edge_id,
            )
            steps.append(observation_step.to_dict())
            yield observation_step

        is_error = str(tool_output or "").startswith("Tool execution failed:")
        helper._record_tool_call_history(
            session_id=self.session.id,
            message_id=self.assistant_message_id,
            agent_type="graph_tool",
            iteration=sequence,
            tool_name=tool_name,
            success=not is_error,
            failure_reason=tool_output if is_error else None,
        )

        yield NodeResult(
            status="error" if is_error else "completed",
            output=tool_output,
            state_patch={},
            steps=steps,
            error={"tool": tool_name, "message": tool_output} if is_error else None,
        )

    async def _execute_router_node(self, node: Dict[str, Any], sequence: int) -> AsyncGenerator[Any, None]:
        router_step = self._enrich_step(
            AgentStep(
                step_type="observation",
                content="Router evaluated.",
                metadata={"router": True, "iteration": sequence},
            ),
            node_id=node["id"],
            node_type=node["type"],
            node_status="running",
            edge_id=self.incoming_edge_id,
        )
        yield router_step
        yield NodeResult(
            status="completed",
            output=None,
            state_patch={},
            steps=[router_step.to_dict()],
            error=None,
        )

    def _execute_node_stream(self, node: Dict[str, Any], sequence: int) -> AsyncGenerator[Any, None]:
        node_type = str(node.get("type") or "")
        if node_type == "react_agent":
            return self._execute_react_node(node, sequence)
        if node_type == "tool_call":
            return self._execute_tool_call_node(node, sequence)
        if node_type == "router":
            return self._execute_router_node(node, sequence)
        raise RuntimeError(f"Unsupported node type '{node_type}'")

    async def run(self) -> AsyncGenerator[AgentStep, None]:
        await self._initialize_graph_run()

        if self.active_node_id == GRAPH_START:
            try:
                start_edge = self._select_edge(GRAPH_START, self.last_result)
            except Exception as exc:
                error_text = str(exc)
                session_repository.update_graph_run(
                    self.graph_run.id,
                    state_json=deepcopy(self.state),
                    active_node_id=GRAPH_START,
                    status="failed",
                    hop_count=self.hop_count,
                    last_result=self.last_result,
                    error={"message": error_text},
                    completed_at=datetime.now().isoformat(),
                )
                self.final_answer = error_text
                yield self._enrich_step(
                    AgentStep(step_type="error", content=error_text, metadata={"node_id": GRAPH_START}),
                    node_id=GRAPH_START,
                    node_type="start",
                    node_status="error",
                    edge_id=None,
                )
                return
            self.active_node_id = str(start_edge.get("target"))
            self.incoming_edge_id = start_edge.get("id")
            session_repository.update_graph_run(
                self.graph_run.id,
                state_json=deepcopy(self.state),
                active_node_id=self.active_node_id,
                status="running",
                hop_count=self.hop_count,
                last_result=self.last_result,
            )

        max_hops = int(self.graph.get("max_hops", 100) or 100)
        while self.active_node_id and self.active_node_id != GRAPH_END:
            if self.hop_count >= max_hops:
                error_text = f"Graph exceeded max_hops={max_hops}"
                failure_step = self._enrich_step(
                    AgentStep(step_type="error", content=error_text, metadata={"max_hops": max_hops}),
                    node_id=str(self.active_node_id),
                    node_type=str(self.nodes.get(str(self.active_node_id), {}).get("type") or "unknown"),
                    node_status="error",
                    edge_id=self.incoming_edge_id,
                )
                session_repository.update_graph_run(
                    self.graph_run.id,
                    state_json=deepcopy(self.state),
                    active_node_id=str(self.active_node_id),
                    status="failed",
                    hop_count=self.hop_count,
                    last_result=self.last_result,
                    error={"message": error_text},
                    completed_at=datetime.now().isoformat(),
                )
                self.final_answer = error_text
                yield failure_step
                return

            node = self.nodes.get(str(self.active_node_id))
            if node is None:
                error_text = f"Unknown active node '{self.active_node_id}'"
                failure_step = self._enrich_step(
                    AgentStep(step_type="error", content=error_text, metadata={}),
                    node_id=str(self.active_node_id),
                    node_type="unknown",
                    node_status="error",
                    edge_id=self.incoming_edge_id,
                )
                session_repository.update_graph_run(
                    self.graph_run.id,
                    state_json=deepcopy(self.state),
                    active_node_id=str(self.active_node_id),
                    status="failed",
                    hop_count=self.hop_count,
                    last_result=self.last_result,
                    error={"message": error_text},
                    completed_at=datetime.now().isoformat(),
                )
                self.final_answer = error_text
                yield failure_step
                return

            sequence = self.hop_count + 1
            self.last_node_id = node["id"]
            self.last_node_type = node["type"]
            self._active_node_state_patch = {}
            self._active_node_profile_id = None
            node_run = session_repository.create_graph_node_run(
                GraphNodeRun(
                    graph_run_id=self.graph_run.id,
                    node_id=node["id"],
                    node_type=node["type"],
                    sequence=sequence,
                    status="running",
                    input_json={
                        "state": deepcopy(self.state),
                        "state_schema": deepcopy(self.state_schema),
                        "last_result": deepcopy(self.last_result),
                    },
                )
            )
            started = time.monotonic()

            node_result: Optional[NodeResult] = None
            try:
                async for item in self._execute_node_stream(node, sequence):
                    if isinstance(item, NodeResult):
                        node_result = item
                        continue
                    yield item
            except Exception as exc:
                node_result = NodeResult(
                    status="error",
                    output=str(exc),
                    state_patch=deepcopy(self._active_node_state_patch),
                    steps=[],
                    error={"message": str(exc)},
                )

            if node_result is None:
                node_result = NodeResult(
                    status="error",
                    output="Node execution produced no result",
                    state_patch=deepcopy(self._active_node_state_patch),
                    steps=[],
                    error={"message": "Node execution produced no result"},
                )

            if isinstance(node_result.state_patch, dict) and node_result.state_patch:
                self.state = _merge_state(self.state, node_result.state_patch)
            if node.get("output_path") and node.get("type") != "router":
                output_path = str(node.get("output_path"))
                if _is_reserved_state_path(output_path):
                    raise RuntimeError(f"output_path '{output_path}' targets runtime-managed state")
                self.state = _set_state_path(self.state, output_path, node_result.output)
            if node.get("type") == "react_agent":
                self._append_react_node_message(node, node_result)

            self.last_result = _node_result_to_dict(node_result)
            if any(isinstance(step, dict) and step.get("step_type") == "answer" for step in (node_result.steps or [])):
                self.final_answer = _value_to_text(node_result.output)
            duration_ms = int((time.monotonic() - started) * 1000)
            session_repository.update_graph_node_run(
                node_run.id,
                status=node_result.status,
                output_json={"status": node_result.status, "output": node_result.output},
                state_patch_json=node_result.state_patch,
                error_json=node_result.error,
                completed_at=datetime.now().isoformat(),
                duration_ms=duration_ms,
            )

            try:
                next_edge = self._select_edge(node["id"], self.last_result)
            except Exception as exc:
                error_text = str(exc)
                session_repository.update_graph_run(
                    self.graph_run.id,
                    state_json=deepcopy(self.state),
                    active_node_id=node["id"],
                    status="failed",
                    hop_count=sequence,
                    last_result=self.last_result,
                    error={"message": error_text},
                    completed_at=datetime.now().isoformat(),
                )
                failure_step = self._enrich_step(
                    AgentStep(step_type="error", content=error_text, metadata={"node_id": node["id"]}),
                    node_id=node["id"],
                    node_type=node["type"],
                    node_status="error",
                    edge_id=self.incoming_edge_id,
                )
                self.final_answer = _value_to_text(node_result.output) or error_text
                yield failure_step
                return

            self.hop_count = sequence
            self.active_node_id = str(next_edge.get("target"))
            self.incoming_edge_id = next_edge.get("id")
            next_status = "completed" if self.active_node_id == GRAPH_END else "running"
            session_repository.update_graph_run(
                self.graph_run.id,
                state_json=deepcopy(self.state),
                active_node_id=self.active_node_id,
                status=next_status,
                hop_count=self.hop_count,
                last_result=self.last_result,
                error=node_result.error if node_result.status == "error" and self.active_node_id == GRAPH_END else None,
                completed_at=datetime.now().isoformat() if self.active_node_id == GRAPH_END else None,
            )

        if self.final_answer is None and self.last_result is not None:
            final_text = _value_to_text(self.last_result.get("output"))
            last_steps = self.last_result.get("steps") if isinstance(self.last_result, dict) else []
            last_has_answer = any(isinstance(step, dict) and step.get("step_type") == "answer" for step in (last_steps or []))
            if final_text and not last_has_answer:
                self.final_answer = final_text
                yield self._enrich_step(
                    AgentStep(step_type="answer", content=final_text, metadata={}),
                    node_id=self.last_node_id or GRAPH_END,
                    node_type=self.last_node_type or "graph",
                    node_status="completed",
                    edge_id=self.incoming_edge_id,
                )
