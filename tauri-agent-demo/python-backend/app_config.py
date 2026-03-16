import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from graph_runtime.expression import validate_edge_expression

from runtime_paths import get_app_config_path as resolve_runtime_app_config_path

GRAPH_START = "__start__"
GRAPH_END = "__end__"
DEFAULT_GRAPH_ID = "default_linear_react"
STATE_FIELD_TYPES = {"string", "number", "boolean", "object", "array", "any"}
RESERVED_STATE_ROOTS = {"input", "messages"}
GRAPH_TEMPLATE_ROOTS = {"state", "result", "session"}
_GRAPH_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


_DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "llm": {
        "timeout_sec": 180.0,
        "reasoning_summary": "detailed",
        "auto_title_enabled": True
    },
    "context": {
        "compression_enabled": False,
        "compress_start_pct": 75,
        "compress_target_pct": 55,
        "min_keep_messages": 12,
        "keep_recent_calls": 10,
        "step_calls": 5,
        "truncate_long_data": True,
        "long_data_threshold": 4000,
        "long_data_head_chars": 1200,
        "long_data_tail_chars": 800
    },
    "agent": {
        "base_system_prompt": "You are a helpful AI assistant.",
        "react_max_iterations": 50,
        "ast_enabled": True,
        "subagent_profile": "subagent",
        "code_map": {
            "enabled": True,
            "max_symbols": 40,
            "max_files": 20,
            "max_lines": 30,
            "weight_refs": 1.0,
            "weight_mentions": 2.0
        },
        "mcp": {
            "servers": []
        },
        "abilities": [
            {
                "id": "tools_all",
                "name": "All Tools",
                "type": "tooling",
                "tools": ["*"],
                "prompt": ""
            },
            {
                "id": "rg_search",
                "name": "RG Search",
                "type": "tooling",
                "tools": ["rg"],
                "prompt": "Prefer rg for searching file contents."
            },
            {
                "id": "apply_patch",
                "name": "Apply Patch",
                "type": "tooling",
                "tools": ["apply_patch"],
                "prompt": "Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\napply_patch format (strict):\n  *** Begin Patch\n  *** Update File: path\n  @@\n  - old line\n  + new line\n  *** End Patch\n- Each change line must start with + or -, and context lines must be included under @@ hunks.\n- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n- apply_patch matches by context; if the match is not unique, request more surrounding context.\n- If apply_patch fails due to context, ask for more context and retry."
            },
            {
                "id": "pty_status",
                "name": "Live PTY",
                "type": "workflow",
                "prompt": "Live PTY sessions for this chat:\n{{pty_sessions}}"
            },
            {
                "id": "code_map",
                "name": "Code Map",
                "type": "domain_knowledge",
                "prompt": "{{code_map_prompt}}"
            },
            {
                "id": "tool_json",
                "name": "Tool Arguments JSON",
                "type": "tool_policy",
                "prompt": "If a tool is needed, call it with JSON arguments that match its schema."
            },
            {
                "id": "output_concise",
                "name": "Concise Output",
                "type": "output_format",
                "prompt": "Be concise and actionable."
            },
            {
                "id": "file_references",
                "name": "File References",
                "type": "output_format",
                "prompt": "File References: When referencing files in your response, make sure to include the relevant start line and always follow the below rules:\n- Use inline code to make file paths clickable.\n- Each reference should have a stand alone path. Even if it's the same file.\n- Accepted: absolute, workspace-relative, a/ or b/ diff prefixes, or bare filename/suffix.\n- Line/column (1-based, optional): :line[:column] or #Lline[Ccolumn] (column defaults to 1).\n- Do not use URIs like file://, vscode://, or https://.\n- Do not provide range of lines.\n- Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\\repo\\project\\main.rs:12:5"
            },
            {
                "id": "read_only_tools",
                "name": "Read Only Tools",
                "type": "tooling",
                "tools": ["rg", "search", "read_file", "list_files", "code_ast"],
                "prompt": "Use read-only tools to inspect the repository before making recommendations."
            },
            {
                "id": "handoff_tool",
                "name": "Handoff Tool",
                "type": "tooling",
                "tools": ["handoff"],
                "prompt": "When another specialist should continue, use handoff instead of continuing outside your role."
            },
            {
                "id": "shell_exec",
                "name": "Shell Execution",
                "type": "tooling",
                "tools": ["run_shell"],
                "prompt": "Use shell commands when validation or test execution is required."
            },
            {
                "id": "planner_workflow",
                "name": "Planner Workflow",
                "type": "workflow",
                "prompt": "Break the request into concrete tasks, decide which agent should own each task, and hand off once the plan is ready. Do not modify files directly."
            },
            {
                "id": "planner_constraints",
                "name": "Planner Constraints",
                "type": "constraints",
                "prompt": "Stay focused on scoping, sequencing, and delegation. Do not claim implementation or test results you did not personally verify."
            },
            {
                "id": "coder_workflow",
                "name": "Coder Workflow",
                "type": "workflow",
                "prompt": "Implement the agreed changes, inspect existing code before editing, and leave the repository in a runnable state when possible."
            },
            {
                "id": "reviewer_workflow",
                "name": "Reviewer Workflow",
                "type": "workflow",
                "prompt": "Review the implementation critically, look for bugs and regressions, and hand off with precise findings. Do not edit project files directly."
            },
            {
                "id": "reviewer_constraints",
                "name": "Reviewer Constraints",
                "type": "constraints",
                "prompt": "Focus on code review, risk identification, and actionable feedback. Validate claims against the repository or command output before reporting them."
            },
            {
                "id": "tester_workflow",
                "name": "Tester Workflow",
                "type": "workflow",
                "prompt": "Validate the implementation by running relevant checks or tests, summarize failures precisely, and hand off when fixes are required."
            },
            {
                "id": "tester_constraints",
                "name": "Tester Constraints",
                "type": "constraints",
                "prompt": "Focus on verification and diagnosis. Do not modify project files directly."
            }
        ],
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "description": "Primary profile for the main assistant.",
                "abilities": ["tools_all", "rg_search", "apply_patch", "pty_status", "code_map", "tool_json", "output_concise", "file_references"],
                "spawnable": False
            },
            {
                "id": "subagent",
                "name": "Subagent",
                "description": "Spawnable profile for delegated tasks.",
                "abilities": ["tools_all", "rg_search", "apply_patch", "pty_status", "code_map", "tool_json", "output_concise", "file_references"],
                "spawnable": True
            },
            {
                "id": "planner",
                "name": "Planner",
                "description": "Breaks down user requests into a plan and routes work to the right specialist.",
                "abilities": ["read_only_tools", "handoff_tool", "rg_search", "pty_status", "code_map", "tool_json", "output_concise", "file_references", "planner_workflow", "planner_constraints"],
                "spawnable": False
            },
            {
                "id": "coder",
                "name": "Coder",
                "description": "Implements code changes and repository updates.",
                "abilities": ["tools_all", "rg_search", "apply_patch", "pty_status", "code_map", "tool_json", "output_concise", "file_references", "coder_workflow"],
                "spawnable": False
            },
            {
                "id": "reviewer",
                "name": "Reviewer",
                "description": "Reviews implementations, highlights risks, and decides whether work is ready for validation.",
                "abilities": ["read_only_tools", "handoff_tool", "rg_search", "shell_exec", "pty_status", "code_map", "tool_json", "output_concise", "file_references", "reviewer_workflow", "reviewer_constraints"],
                "spawnable": False
            },
            {
                "id": "tester",
                "name": "Tester",
                "description": "Runs tests, validates behavior, and reports failures without editing code.",
                "abilities": ["read_only_tools", "shell_exec", "handoff_tool", "rg_search", "pty_status", "code_map", "tool_json", "output_concise", "file_references", "tester_workflow", "tester_constraints"],
                "spawnable": False
            }
        ],
        "team": {
            "execution_mode": "single_session",
            "members": [
                {
                    "profile_id": "default",
                    "handoff_to": ["subagent", "planner", "coder", "reviewer", "tester"]
                },
                {
                    "profile_id": "subagent",
                    "handoff_to": ["default"]
                },
                {
                    "profile_id": "planner",
                    "handoff_to": ["coder", "reviewer", "tester", "default"]
                },
                {
                    "profile_id": "coder",
                    "handoff_to": ["reviewer", "tester", "planner", "default"]
                },
                {
                    "profile_id": "reviewer",
                    "handoff_to": ["coder", "tester", "planner", "default"]
                },
                {
                    "profile_id": "tester",
                    "handoff_to": ["planner", "coder", "reviewer", "default"]
                }
            ],
            "default_agent": "default"
        },
        "teams": [
            {
                "id": "delivery",
                "name": "Delivery Team",
                "leader_profile_id": "planner",
                "member_profile_ids": ["planner", "coder", "reviewer", "tester"],
                "description": "Planner -> Coder -> Reviewer -> Tester"
            }
        ],
        "default_profile": "default",
        "state_presets": [],
        "graphs": [
            {
                "id": DEFAULT_GRAPH_ID,
                "name": "Default Delivery Graph",
                "initial_state": {
                    "message": None,
                    "current_task": None,
                },
                "state_schema": [
                    {"path": "message", "type": "string"},
                    {"path": "current_task", "type": "string"},
                ],
                "max_hops": 100,
                "nodes": [
                    {
                        "id": "planner",
                        "type": "react_agent",
                        "name": "Planner",
                        "profile_id": "planner",
                        "input_template": "{{state.input.user_message}}",
                    },
                    {
                        "id": "coder",
                        "type": "react_agent",
                        "name": "Coder",
                        "profile_id": "coder",
                        "input_template": "{{state.input.user_message}}",
                    },
                    {
                        "id": "reviewer",
                        "type": "react_agent",
                        "name": "Reviewer",
                        "profile_id": "reviewer",
                        "input_template": "{{state.input.user_message}}",
                    },
                    {
                        "id": "tester",
                        "type": "react_agent",
                        "name": "Tester",
                        "profile_id": "tester",
                        "input_template": "{{state.input.user_message}}",
                        "output_path": "last_answer",
                    }
                ],
                "edges": [
                    {
                        "id": "start_to_planner",
                        "source": GRAPH_START,
                        "target": "planner",
                        "priority": 0,
                    },
                    {
                        "id": "planner_to_coder",
                        "source": "planner",
                        "target": "coder",
                        "priority": 0,
                    },
                    {
                        "id": "coder_to_reviewer",
                        "source": "coder",
                        "target": "reviewer",
                        "priority": 0,
                    },
                    {
                        "id": "reviewer_to_tester",
                        "source": "reviewer",
                        "target": "tester",
                        "priority": 0,
                    },
                    {
                        "id": "tester_to_end",
                        "source": "tester",
                        "target": GRAPH_END,
                        "priority": 0,
                    },
                ],
            }
        ],
        "default_graph_id": DEFAULT_GRAPH_ID,
    }
}

_CONFIG_PATH_OVERRIDE: Optional[Path] = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_config_file_path() -> Path:
    global _CONFIG_PATH_OVERRIDE
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE
    return resolve_runtime_app_config_path()


def get_app_config_path() -> str:
    return str(_get_config_file_path())


def _load_config_file() -> Dict[str, Any]:
    path = _get_config_file_path()
    if path.exists() and not path.is_dir():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _coerce_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError("llm.timeout_sec must be a number")
    if timeout <= 0:
        raise ValueError("llm.timeout_sec must be positive")
    if timeout > 3600:
        raise ValueError("llm.timeout_sec must be <= 3600 seconds")
    return timeout


def _coerce_reasoning_summary(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("llm.reasoning_summary must be a string")
    normalized = value.strip().lower()
    if normalized not in ("auto", "concise", "detailed"):
        raise ValueError("llm.reasoning_summary must be one of: auto, concise, detailed")
    return normalized


def _coerce_react_max_iterations(value: Any) -> int:
    try:
        max_iterations = int(value)
    except (TypeError, ValueError):
        raise ValueError("agent.react_max_iterations must be an integer")
    if max_iterations < 1:
        raise ValueError("agent.react_max_iterations must be >= 1")
    return max_iterations


def _coerce_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "y", "on"):
            return True
        if lowered in ("false", "0", "no", "n", "off"):
            return False
    raise ValueError(f"{field} must be a boolean")


def _coerce_int_range(value: Any, field: str, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{field} must be between {min_value} and {max_value}")
    return parsed


def _coerce_int_min(value: Any, field: str, min_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if parsed < min_value:
        raise ValueError(f"{field} must be >= {min_value}")
    return parsed


def _clone_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _coerce_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")


def _normalize_graph_node_ui(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    normalized: Dict[str, Any] = {}
    position = value.get("position")
    if position is not None:
        if not isinstance(position, dict):
            raise ValueError(f"{field}.position must be an object")
        normalized["position"] = {
            "x": _coerce_float(position.get("x"), f"{field}.position.x"),
            "y": _coerce_float(position.get("y"), f"{field}.position.y"),
        }
    return normalized


def _normalize_graph_definition_ui(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    normalized: Dict[str, Any] = {}
    viewport = value.get("viewport")
    if viewport is not None:
        if not isinstance(viewport, dict):
            raise ValueError(f"{field}.viewport must be an object")
        zoom = _coerce_float(viewport.get("zoom"), f"{field}.viewport.zoom")
        if zoom <= 0:
            raise ValueError(f"{field}.viewport.zoom must be > 0")
        normalized["viewport"] = {
            "x": _coerce_float(viewport.get("x"), f"{field}.viewport.x"),
            "y": _coerce_float(viewport.get("y"), f"{field}.viewport.y"),
            "zoom": zoom,
        }
    return normalized


def _normalize_state_preset(
    value: Any,
    preset_index: int,
    seen_ids: Set[str],
    seen_names: Set[str],
) -> Dict[str, Any]:
    field_prefix = f"agent.state_presets[{preset_index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field_prefix} must be an object")

    preset_id = _ensure_non_empty_string(value.get("id"), f"{field_prefix}.id")
    if preset_id in seen_ids:
        raise ValueError("agent.state_presets.id must be unique")
    seen_ids.add(preset_id)

    preset_name = _ensure_non_empty_string(value.get("name"), f"{field_prefix}.name")
    normalized_name = preset_name.lower()
    if normalized_name in seen_names:
        raise ValueError("agent.state_presets.name must be unique")
    seen_names.add(normalized_name)

    normalized_schema = _normalize_state_schema(
        value.get("state_schema"),
        f"{field_prefix}.state_schema",
    )
    state_value = _clone_json_value(value.get("state", {}))
    _validate_no_reserved_state_roots(state_value, f"{field_prefix}.state")
    normalized: Dict[str, Any] = {
        "id": preset_id,
        "name": preset_name,
        "state": state_value,
        "state_schema": normalized_schema,
    }

    description = _normalize_optional_string(value.get("description"), f"{field_prefix}.description")
    if description is not None:
        normalized["description"] = description

    return normalized


def _normalize_state_schema_path(value: Any, field: str) -> str:
    path = _ensure_non_empty_string(value, field)
    normalized = ".".join(part.strip() for part in path.split(".") if part.strip())
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _state_path_root(path: str) -> str:
    return str(path).split(".", 1)[0]


def _is_reserved_state_path(path: str) -> bool:
    return _state_path_root(path) in RESERVED_STATE_ROOTS


def _validate_no_reserved_state_roots(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        return
    conflicts = sorted(str(key) for key in value.keys() if str(key) in RESERVED_STATE_ROOTS)
    if conflicts:
        raise ValueError(
            f"{field} cannot define reserved runtime state root(s): {', '.join(conflicts)}"
        )


def _validate_graph_template_roots(value: Any, field: str) -> None:
    if isinstance(value, str):
        for match in _GRAPH_TEMPLATE_PATTERN.finditer(value):
            expression = str(match.group(1) or "").strip()
            if not expression:
                continue
            root = expression.split(".", 1)[0]
            if root not in GRAPH_TEMPLATE_ROOTS:
                raise ValueError(
                    f"{field} only supports template roots: {', '.join(sorted(GRAPH_TEMPLATE_ROOTS))}"
                )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_graph_template_roots(item, f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_graph_template_roots(item, f"{field}.{key}")


def _normalize_state_schema(value: Any, field: str) -> List[Dict[str, Any]]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")

    normalized: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_field} must be an object")
        path = _normalize_state_schema_path(item.get("path"), f"{item_field}.path")
        if path in seen_paths:
            raise ValueError(f"{field}.path must be unique")
        seen_paths.add(path)
        field_type = _ensure_non_empty_string(item.get("type"), f"{item_field}.type").lower()
        if field_type not in STATE_FIELD_TYPES:
            raise ValueError(
                f"{item_field}.type must be one of: {', '.join(sorted(STATE_FIELD_TYPES))}"
            )
        if _is_reserved_state_path(path):
            raise ValueError(f"{item_field}.path cannot use reserved runtime state path '{path}'")
        mutable = False
        if item.get("mutable") is not None:
            mutable = _coerce_bool(item.get("mutable"), f"{item_field}.mutable")
        normalized.append({"path": path, "type": field_type, "mutable": mutable})
    return normalized


def _default_graph_definition() -> Dict[str, Any]:
    agent_defaults = _DEFAULT_APP_CONFIG.get("agent", {})
    graphs = agent_defaults.get("graphs") if isinstance(agent_defaults, dict) else None
    if isinstance(graphs, list) and graphs:
        return _clone_json_value(graphs[0])
    raise ValueError("Default graph definition is missing")


def _ensure_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _normalize_optional_string(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    return normalized or None


def _normalize_graph_node(value: Any, graph_index: int, node_index: int, seen_ids: Set[str]) -> Dict[str, Any]:
    field_prefix = f"agent.graphs[{graph_index}].nodes[{node_index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field_prefix} must be an object")

    node_id = _ensure_non_empty_string(value.get("id"), f"{field_prefix}.id")
    if node_id in (GRAPH_START, GRAPH_END):
        raise ValueError(f"{field_prefix}.id cannot use reserved id {node_id}")
    if node_id in seen_ids:
        raise ValueError(f"{field_prefix}.id must be unique within the graph")
    seen_ids.add(node_id)

    node_type = _ensure_non_empty_string(value.get("type"), f"{field_prefix}.type")
    if node_type not in ("react_agent", "tool_call", "router"):
        raise ValueError(f"{field_prefix}.type must be one of: react_agent, tool_call, router")

    normalized: Dict[str, Any] = {
        "id": node_id,
        "type": node_type,
    }

    for key in ("name", "description", "profile_id", "output_path", "tool_name"):
        normalized_value = _normalize_optional_string(value.get(key), f"{field_prefix}.{key}")
        if normalized_value is not None:
            normalized[key] = normalized_value

    if normalized.get("output_path") and _is_reserved_state_path(str(normalized.get("output_path"))):
        raise ValueError(
            f"{field_prefix}.output_path cannot target reserved runtime state path '{normalized['output_path']}'"
        )

    if "input_template" in value:
        normalized["input_template"] = value.get("input_template")
    if "args_template" in value:
        normalized["args_template"] = value.get("args_template")

    if value.get("max_iterations") is not None:
        normalized["max_iterations"] = _coerce_int_min(
            value.get("max_iterations"),
            f"{field_prefix}.max_iterations",
            1,
        )
    if value.get("ui") is not None:
        normalized_ui = _normalize_graph_node_ui(value.get("ui"), f"{field_prefix}.ui")
        if normalized_ui:
            normalized["ui"] = normalized_ui

    if node_type == "tool_call":
        if not normalized.get("tool_name"):
            raise ValueError(f"{field_prefix}.tool_name is required for tool_call nodes")
    else:
        normalized.pop("tool_name", None)

    if node_type == "react_agent":
        if "args_template" in normalized:
            normalized.pop("args_template", None)
        normalized.pop("tool_name", None)
    else:
        normalized.pop("profile_id", None)
        normalized.pop("max_iterations", None)
        if node_type != "router":
            normalized.pop("input_template", None)

    if node_type == "router":
        normalized.pop("profile_id", None)
        normalized.pop("max_iterations", None)
        normalized.pop("tool_name", None)
        normalized.pop("args_template", None)
        normalized.pop("input_template", None)
        normalized.pop("output_path", None)

    if "input_template" in normalized:
        _validate_graph_template_roots(normalized["input_template"], f"{field_prefix}.input_template")
    if "args_template" in normalized:
        _validate_graph_template_roots(normalized["args_template"], f"{field_prefix}.args_template")

    return normalized


def _normalize_graph_edge(
    value: Any,
    graph_index: int,
    edge_index: int,
    valid_node_ids: Set[str],
) -> Dict[str, Any]:
    field_prefix = f"agent.graphs[{graph_index}].edges[{edge_index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field_prefix} must be an object")

    source = _ensure_non_empty_string(value.get("source"), f"{field_prefix}.source")
    target = _ensure_non_empty_string(value.get("target"), f"{field_prefix}.target")
    if source == GRAPH_END:
        raise ValueError(f"{field_prefix}.source cannot be {GRAPH_END}")
    if target == GRAPH_START:
        raise ValueError(f"{field_prefix}.target cannot be {GRAPH_START}")
    if source != GRAPH_START and source not in valid_node_ids:
        raise ValueError(f"{field_prefix}.source references unknown node '{source}'")
    if target != GRAPH_END and target not in valid_node_ids:
        raise ValueError(f"{field_prefix}.target references unknown node '{target}'")

    edge_id = _normalize_optional_string(value.get("id"), f"{field_prefix}.id")
    if edge_id is None:
        edge_id = f"{source}_to_{target}_{edge_index}"

    priority = 0
    if value.get("priority") is not None:
        priority = _coerce_int_min(value.get("priority"), f"{field_prefix}.priority", 0)

    condition = _normalize_optional_string(value.get("condition"), f"{field_prefix}.condition")
    if condition is not None:
        validate_edge_expression(condition)

    normalized: Dict[str, Any] = {
        "id": edge_id,
        "source": source,
        "target": target,
        "priority": priority,
    }
    if condition is not None:
        normalized["condition"] = condition
    label = _normalize_optional_string(value.get("label"), f"{field_prefix}.label")
    if label is not None:
        normalized["label"] = label
    return normalized


def _graph_has_path_to_end(
    outgoing: Dict[str, List[Dict[str, Any]]],
    current: str,
    visiting: Optional[Set[str]] = None,
    visited: Optional[Set[str]] = None,
) -> bool:
    if current == GRAPH_END:
        return True
    visiting = visiting or set()
    visited = visited or set()
    if current in visiting:
        return False
    if current in visited:
        return False
    visiting.add(current)
    for edge in outgoing.get(current, []):
        target = edge.get("target")
        if isinstance(target, str) and _graph_has_path_to_end(outgoing, target, visiting, visited):
            return True
    visiting.remove(current)
    visited.add(current)
    return False


def _normalize_graph_definition(
    value: Any,
    graph_index: int,
    preset_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    field_prefix = f"agent.graphs[{graph_index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field_prefix} must be an object")

    graph_id = _ensure_non_empty_string(value.get("id"), f"{field_prefix}.id")
    graph_name = _ensure_non_empty_string(value.get("name"), f"{field_prefix}.name")

    max_hops = 100
    if value.get("max_hops") is not None:
        max_hops = _coerce_int_range(value.get("max_hops"), f"{field_prefix}.max_hops", 1, 10000)
    normalized_ui = None
    if value.get("ui") is not None:
        normalized_ui = _normalize_graph_definition_ui(value.get("ui"), f"{field_prefix}.ui")

    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError(f"{field_prefix}.nodes must be a non-empty list")

    node_ids: Set[str] = set()
    normalized_nodes = [
        _normalize_graph_node(node, graph_index, node_index, node_ids)
        for node_index, node in enumerate(raw_nodes)
    ]

    raw_edges = value.get("edges")
    if not isinstance(raw_edges, list) or not raw_edges:
        raise ValueError(f"{field_prefix}.edges must be a non-empty list")

    normalized_edges = [
        _normalize_graph_edge(edge, graph_index, edge_index, node_ids)
        for edge_index, edge in enumerate(raw_edges)
    ]

    edge_ids: Set[str] = set()
    fallback_by_source: Dict[str, int] = {}
    outgoing: Dict[str, List[Dict[str, Any]]] = {}
    has_start_edge = False
    for edge in normalized_edges:
        edge_id = edge["id"]
        if edge_id in edge_ids:
            raise ValueError(f"{field_prefix}.edges.id must be unique within the graph")
        edge_ids.add(edge_id)

        source = edge["source"]
        outgoing.setdefault(source, []).append(edge)
        if source == GRAPH_START:
            has_start_edge = True
        if not edge.get("condition"):
            fallback_by_source[source] = fallback_by_source.get(source, 0) + 1
            if fallback_by_source[source] > 1:
                raise ValueError(
                    f"{field_prefix}.edges allows at most one fallback edge per source node"
                )

    if not has_start_edge:
        raise ValueError(f"{field_prefix} must define at least one edge from {GRAPH_START}")

    if not _graph_has_path_to_end(outgoing, GRAPH_START):
        raise ValueError(f"{field_prefix} must have a path from {GRAPH_START} to {GRAPH_END}")

    initial_state = _clone_json_value(value.get("initial_state", {}))
    _validate_no_reserved_state_roots(initial_state, f"{field_prefix}.initial_state")
    normalized_graph = {
        "id": graph_id,
        "name": graph_name,
        "initial_state": initial_state,
        "state_schema": _normalize_state_schema(
            value.get("state_schema"),
            f"{field_prefix}.state_schema",
        ),
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "max_hops": max_hops,
    }
    state_preset_id = _normalize_optional_string(value.get("state_preset_id"), f"{field_prefix}.state_preset_id")
    if state_preset_id is not None:
        if preset_ids is not None and state_preset_id not in preset_ids:
            raise ValueError(f"{field_prefix}.state_preset_id references unknown preset '{state_preset_id}'")
        normalized_graph["state_preset_id"] = state_preset_id
    if normalized_ui:
        normalized_graph["ui"] = normalized_ui
    return normalized_graph


def _normalize_graphs(agent: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(agent)
    raw_state_presets = normalized.get("state_presets")
    if raw_state_presets is None:
        raw_state_presets = []
    if not isinstance(raw_state_presets, list):
        raise ValueError("agent.state_presets must be a list")

    preset_ids: Set[str] = set()
    preset_names: Set[str] = set()
    normalized["state_presets"] = [
        _normalize_state_preset(preset, preset_index, preset_ids, preset_names)
        for preset_index, preset in enumerate(raw_state_presets)
    ]

    raw_graphs = normalized.get("graphs")
    if raw_graphs is None:
        raw_graphs = []
    if not isinstance(raw_graphs, list):
        raise ValueError("agent.graphs must be a list")

    if not raw_graphs:
        normalized["graphs"] = [_default_graph_definition()]
    else:
        graph_ids: Set[str] = set()
        normalized_graphs = []
        for graph_index, graph in enumerate(raw_graphs):
            normalized_graph = _normalize_graph_definition(graph, graph_index, preset_ids)
            graph_id = normalized_graph["id"]
            if graph_id in graph_ids:
                raise ValueError("agent.graphs.id must be unique")
            graph_ids.add(graph_id)
            normalized_graphs.append(normalized_graph)
        normalized["graphs"] = normalized_graphs

    default_graph_id = normalized.get("default_graph_id")
    if default_graph_id is None:
        default_graph_id = normalized["graphs"][0]["id"]
    default_graph_id = _ensure_non_empty_string(default_graph_id, "agent.default_graph_id")
    available_graph_ids = {graph["id"] for graph in normalized["graphs"]}
    if default_graph_id not in available_graph_ids:
        raise ValueError("agent.default_graph_id must reference an existing graph")
    normalized["default_graph_id"] = default_graph_id
    return normalized


def _normalize_context_config(context: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(context)
    if "compression_enabled" in normalized:
        normalized["compression_enabled"] = _coerce_bool(
            normalized["compression_enabled"], "context.compression_enabled"
        )
    if "compress_start_pct" in normalized:
        normalized["compress_start_pct"] = _coerce_int_range(
            normalized["compress_start_pct"], "context.compress_start_pct", 1, 100
        )
    if "compress_target_pct" in normalized:
        normalized["compress_target_pct"] = _coerce_int_range(
            normalized["compress_target_pct"], "context.compress_target_pct", 1, 100
        )
    if "min_keep_messages" in normalized:
        normalized["min_keep_messages"] = _coerce_int_min(
            normalized["min_keep_messages"], "context.min_keep_messages", 0
        )
    if "keep_recent_calls" in normalized:
        normalized["keep_recent_calls"] = _coerce_int_min(
            normalized["keep_recent_calls"], "context.keep_recent_calls", 0
        )
    if "step_calls" in normalized:
        normalized["step_calls"] = _coerce_int_min(
            normalized["step_calls"], "context.step_calls", 1
        )
    if "truncate_long_data" in normalized:
        normalized["truncate_long_data"] = _coerce_bool(
            normalized["truncate_long_data"], "context.truncate_long_data"
        )
    if "long_data_threshold" in normalized:
        normalized["long_data_threshold"] = _coerce_int_range(
            normalized["long_data_threshold"], "context.long_data_threshold", 200, 200000
        )
    if "long_data_head_chars" in normalized:
        normalized["long_data_head_chars"] = _coerce_int_range(
            normalized["long_data_head_chars"], "context.long_data_head_chars", 0, 200000
        )
    if "long_data_tail_chars" in normalized:
        normalized["long_data_tail_chars"] = _coerce_int_range(
            normalized["long_data_tail_chars"], "context.long_data_tail_chars", 0, 200000
        )

    start_pct = normalized.get("compress_start_pct")
    target_pct = normalized.get("compress_target_pct")
    if start_pct is not None and target_pct is not None and target_pct >= start_pct:
        raise ValueError("context.compress_target_pct must be less than context.compress_start_pct")

    keep_recent_calls = normalized.get("keep_recent_calls")
    step_calls = normalized.get("step_calls")
    if (
        keep_recent_calls is not None
        and step_calls is not None
        and keep_recent_calls > 0
        and step_calls > keep_recent_calls
    ):
        raise ValueError("context.step_calls must be <= context.keep_recent_calls when keep_recent_calls > 0")

    threshold = normalized.get("long_data_threshold")
    head_chars = normalized.get("long_data_head_chars")
    tail_chars = normalized.get("long_data_tail_chars")
    if threshold is not None and head_chars is not None and tail_chars is not None:
        if head_chars + tail_chars > threshold:
            raise ValueError("context.long_data_head_chars + context.long_data_tail_chars must be <= context.long_data_threshold")

    return normalized
def _coerce_code_map(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("agent.code_map must be an object")
    result = dict(value)
    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])
    for key, maximum, minimum in (
        ("max_symbols", 200, 1),
        ("max_files", 100, 1),
        ("max_lines", 200, 1)
    ):
        if key in result:
            try:
                num = int(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"agent.code_map.{key} must be an integer")
            if num < minimum or num > maximum:
                raise ValueError(f"agent.code_map.{key} must be between {minimum} and {maximum}")
            result[key] = num
    for key in ("weight_refs", "weight_mentions"):
        if key in result:
            try:
                result[key] = float(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"agent.code_map.{key} must be a number")
    return result


def _normalize_mcp_filter(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    result: Dict[str, Any] = {}
    if "tool_names" in value:
        if not isinstance(value["tool_names"], list):
            raise ValueError(f"{field}.tool_names must be a list of strings")
        names = [
            str(item).strip()
            for item in value["tool_names"]
            if isinstance(item, (str, int, float))
        ]
        names = [name for name in names if name]
        result["tool_names"] = names
    if "read_only" in value:
        result["read_only"] = _coerce_bool(value["read_only"], f"{field}.read_only")
    return result


def _normalize_mcp_server(value: Any, index: int, labels: set) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"agent.mcp.servers[{index}] must be an object")
    server_label = str(value.get("server_label") or "").strip()
    if not server_label:
        raise ValueError(f"agent.mcp.servers[{index}].server_label is required")
    if server_label in labels:
        raise ValueError("agent.mcp.servers.server_label must be unique")
    labels.add(server_label)

    server_url = value.get("server_url")
    connector_id = value.get("connector_id")
    server_url = str(server_url).strip() if server_url is not None else None
    connector_id = str(connector_id).strip() if connector_id is not None else None
    if not server_url:
        server_url = None
    if not connector_id:
        connector_id = None
    if not server_url and not connector_id:
        raise ValueError(
            f"agent.mcp.servers[{index}] must set server_url (connector_id is ignored)"
        )

    enabled = True
    if "enabled" in value:
        enabled = _coerce_bool(value.get("enabled"), f"agent.mcp.servers[{index}].enabled")

    server_description = value.get("server_description")
    if server_description is not None and not isinstance(server_description, str):
        raise ValueError(f"agent.mcp.servers[{index}].server_description must be a string")
    server_description = server_description.strip() if isinstance(server_description, str) else None

    authorization_env = value.get("authorization_env")
    if authorization_env is not None and not isinstance(authorization_env, str):
        raise ValueError(f"agent.mcp.servers[{index}].authorization_env must be a string")
    authorization_env = authorization_env.strip() if isinstance(authorization_env, str) else None

    headers_env = value.get("headers_env")
    if headers_env is not None and not isinstance(headers_env, str):
        raise ValueError(f"agent.mcp.servers[{index}].headers_env must be a string")
    headers_env = headers_env.strip() if isinstance(headers_env, str) else None

    allowed_tools = value.get("allowed_tools")
    normalized_allowed: Any = None
    if allowed_tools is not None:
        if isinstance(allowed_tools, list):
            names = [
                str(item).strip()
                for item in allowed_tools
                if isinstance(item, (str, int, float))
            ]
            names = [name for name in names if name]
            normalized_allowed = names
        elif isinstance(allowed_tools, dict):
            normalized_allowed = _normalize_mcp_filter(
                allowed_tools, f"agent.mcp.servers[{index}].allowed_tools"
            )
        else:
            raise ValueError(f"agent.mcp.servers[{index}].allowed_tools must be a list or object")

    require_approval = value.get("require_approval")
    normalized_require: Any = None
    if require_approval is not None:
        if isinstance(require_approval, str):
            normalized = require_approval.strip().lower()
            if normalized not in ("always", "never"):
                raise ValueError(
                    f"agent.mcp.servers[{index}].require_approval must be always, never, or an object"
                )
            normalized_require = normalized
        elif isinstance(require_approval, dict):
            require_obj: Dict[str, Any] = {}
            for key in ("always", "never"):
                if key in require_approval and require_approval[key] is not None:
                    require_obj[key] = _normalize_mcp_filter(
                        require_approval[key],
                        f"agent.mcp.servers[{index}].require_approval.{key}"
                    )
            normalized_require = require_obj
        else:
            raise ValueError(
                f"agent.mcp.servers[{index}].require_approval must be always, never, or an object"
            )

    normalized: Dict[str, Any] = {
        "server_label": server_label,
        "enabled": enabled
    }
    if server_url:
        normalized["server_url"] = server_url
    if connector_id:
        normalized["connector_id"] = connector_id
    if server_description:
        normalized["server_description"] = server_description
    if authorization_env:
        normalized["authorization_env"] = authorization_env
    if headers_env:
        normalized["headers_env"] = headers_env
    if normalized_allowed is not None:
        normalized["allowed_tools"] = normalized_allowed
    if normalized_require is not None:
        normalized["require_approval"] = normalized_require
    return normalized


def _normalize_mcp_config(mcp: Any) -> Dict[str, Any]:
    if not isinstance(mcp, dict):
        raise ValueError("agent.mcp must be an object")
    servers = mcp.get("servers")
    if servers is None:
        servers = []
    if not isinstance(servers, list):
        raise ValueError("agent.mcp.servers must be a list")
    normalized_servers = []
    labels: set = set()
    for idx, server in enumerate(servers):
        normalized_servers.append(_normalize_mcp_server(server, idx, labels))
    return {"servers": normalized_servers}


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config)
    llm = dict(normalized.get("llm", {}))
    if "timeout_sec" in llm:
        llm["timeout_sec"] = _coerce_timeout(llm["timeout_sec"])
    if "reasoning_summary" in llm:
        llm["reasoning_summary"] = _coerce_reasoning_summary(llm["reasoning_summary"])
    if "auto_title_enabled" in llm:
        llm["auto_title_enabled"] = _coerce_bool(llm["auto_title_enabled"], "llm.auto_title_enabled")
    normalized["llm"] = llm
    context = dict(normalized.get("context", {}))
    normalized["context"] = _normalize_context_config(context)
    agent = dict(normalized.get("agent", {}))
    if "react_max_iterations" in agent:
        agent["react_max_iterations"] = _coerce_react_max_iterations(agent["react_max_iterations"])
    if "ast_enabled" in agent:
        agent["ast_enabled"] = _coerce_bool(agent["ast_enabled"], "agent.ast_enabled")
    if "subagent_profile" in agent:
        if not isinstance(agent["subagent_profile"], str):
            raise ValueError("agent.subagent_profile must be a string")
        agent["subagent_profile"] = agent["subagent_profile"].strip()
    if "code_map" in agent:
        agent["code_map"] = _coerce_code_map(agent["code_map"])
    if "mcp" in agent:
        agent["mcp"] = _normalize_mcp_config(agent["mcp"])
    agent = _normalize_graphs(agent)
    normalized["agent"] = agent
    return normalized


def _load_config() -> Dict[str, Any]:
    merged = _deep_merge(_DEFAULT_APP_CONFIG, _load_config_file())
    return _normalize_config(merged)


_APP_CONFIG = _load_config()


def get_app_config() -> Dict[str, Any]:
    return _APP_CONFIG


def update_app_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _APP_CONFIG
    if not isinstance(patch, dict):
        raise ValueError("Config update must be a JSON object.")
    current_file = _load_config_file()
    merged_file = _deep_merge(current_file, patch)
    merged_file = _normalize_config(merged_file)
    path = _get_config_file_path()
    content = json.dumps(merged_file, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _APP_CONFIG = _load_config()
    return _APP_CONFIG
