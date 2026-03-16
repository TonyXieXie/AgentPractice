import json
from copy import deepcopy
from typing import Any, Dict, Optional

from tools.base import Tool, ToolParameter
from tools.context import get_tool_context


_RESERVED_STATE_ROOTS = ("input", "messages")


def _normalize_path(path: Any) -> str:
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    normalized = ".".join(segment.strip() for segment in path.split(".") if segment.strip())
    if not normalized:
        raise ValueError("path is required")
    return normalized


def _resolve_path(root: Any, path: str) -> Any:
    current = root
    for segment in [part for part in path.split(".") if part]:
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current.get(segment)
            continue
        if isinstance(current, list):
            try:
                index = int(segment)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        return None
    return current


def _is_reserved_path(path: str) -> bool:
    root = path.split(".", 1)[0]
    return root in _RESERVED_STATE_ROOTS


def _coerce_value(raw_value: Any) -> Any:
    if not isinstance(raw_value, str):
        return raw_value
    try:
        return json.loads(raw_value)
    except Exception:
        return raw_value


def _matches_type(expected_type: str, value: Any) -> bool:
    if value is None or expected_type == "any":
        return True
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


class GraphStateTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "graph_state"
        self.description = (
            "Inspect or update graph runtime state. "
            "Use action=describe|get|set. "
            "For set, provide value as raw text for strings or JSON literals for numbers, booleans, objects, arrays, or null."
        )
        self.parameters = [
            ToolParameter(
                name="action",
                type="string",
                description="One of: describe, get, set",
            ),
            ToolParameter(
                name="path",
                type="string",
                description="State path for get or set, for example plan.current_task",
                required=False,
            ),
            ToolParameter(
                name="value",
                type="string",
                description="Value for set. Use plain text for strings or JSON literals for structured values.",
                required=False,
            ),
        ]

    def _get_graph_context(self) -> Dict[str, Any]:
        context = get_tool_context().get("graph_state")
        if not isinstance(context, dict):
            raise RuntimeError("graph_state is only available while a graph react node is running")
        return context

    def _parse_input(self, input_data: str) -> Dict[str, Any]:
        raw = str(input_data or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            lowered = raw.lower()
            if lowered in {"describe", "get", "set"}:
                return {"action": lowered}
            raise ValueError("graph_state expects JSON arguments")
        if not isinstance(parsed, dict):
            raise ValueError("graph_state expects a JSON object")
        return parsed

    async def execute(self, input_data: str) -> str:
        payload = self._parse_input(input_data)
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"describe", "get", "set"}:
            raise ValueError("action must be one of: describe, get, set")

        context = self._get_graph_context()
        get_state = context.get("get_state")
        if not callable(get_state):
            raise RuntimeError("graph_state context is missing get_state")

        state_snapshot = deepcopy(get_state())
        schema_entries = context.get("schema") or []
        schema_by_path = {
            str(entry.get("path")): {
                "type": str(entry.get("type") or "any"),
                "mutable": bool(entry.get("mutable", False)),
            }
            for entry in schema_entries
            if isinstance(entry, dict) and entry.get("path")
        }

        if action == "describe":
            return json.dumps(
                {
                    "state": state_snapshot,
                    "state_schema": deepcopy(schema_entries),
                    "mutable_fields": [
                        path for path, entry in schema_by_path.items() if entry.get("mutable")
                    ],
                    "reserved_read_only_paths": list(_RESERVED_STATE_ROOTS),
                    "notes": [
                        "state.input.* is injected by the runtime and is read-only",
                        "state.messages is maintained by the runtime and is read-only",
                    ],
                },
                ensure_ascii=False,
            )

        path = _normalize_path(payload.get("path"))
        if action == "get":
            return json.dumps(
                {
                    "path": path,
                    "value": _resolve_path(state_snapshot, path),
                },
                ensure_ascii=False,
            )

        if _is_reserved_path(path):
            raise ValueError(f"path '{path}' is runtime-managed and read-only")
        field_schema = schema_by_path.get(path)
        if not field_schema or not field_schema.get("mutable"):
            raise ValueError(f"path '{path}' is not mutable in the graph state schema")
        if "value" not in payload:
            raise ValueError("set requires a value")
        next_value = _coerce_value(payload.get("value"))
        expected_type = str(field_schema.get("type") or "any")
        if not _matches_type(expected_type, next_value):
            raise ValueError(f"value for '{path}' must match schema type '{expected_type}'")

        set_state = context.get("set_state")
        if not callable(set_state):
            raise RuntimeError("graph_state context is missing set_state")
        set_state(path, next_value)
        updated_state = deepcopy(get_state())
        return json.dumps(
            {
                "path": path,
                "value": _resolve_path(updated_state, path),
                "status": "updated",
            },
            ensure_ascii=False,
        )
