import ast
import json
from typing import Any, Dict, List, Optional

from tools.base import Tool, ToolParameter
from mcp_client import call_tool


def _normalize_schema_type(value: Any) -> str:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str):
        value = value.lower()
    if value in ("string", "number", "boolean", "object", "array"):
        return value
    if value in ("integer", "int"):
        return "number"
    return "string"


def _build_parameters_from_schema(schema: Optional[Dict[str, Any]]) -> List[ToolParameter]:
    if not isinstance(schema, dict):
        return []
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return [
            ToolParameter(
                name="arguments",
                type="object",
                description="MCP tool arguments.",
                required=False
            )
        ]
    properties = schema.get("properties") or {}
    required_set = set(schema.get("required") or [])
    params: List[ToolParameter] = []
    for key, prop in properties.items():
        if not key:
            continue
        if not isinstance(prop, dict):
            prop = {}
        param_type = _normalize_schema_type(prop.get("type"))
        description = prop.get("description") or ""
        items = prop.get("items") if isinstance(prop.get("items"), dict) else None
        default = prop.get("default") if "default" in prop else None
        params.append(
            ToolParameter(
                name=str(key),
                type=param_type,
                description=str(description),
                required=key in required_set,
                default=default,
                items=items
            )
        )
    if not params:
        return [
            ToolParameter(
                name="arguments",
                type="object",
                description="MCP tool arguments.",
                required=False
            )
        ]
    return params


class MCPTool(Tool):
    def __init__(
        self,
        server_cfg: Dict[str, Any],
        tool_meta: Dict[str, Any],
        safe_name: str,
        display_name: Optional[str] = None
    ):
        super().__init__()
        self.server_cfg = server_cfg
        self.tool_meta = tool_meta or {}
        self.server_label = str(server_cfg.get("server_label") or "").strip() or "mcp"
        self.tool_name = str(self.tool_meta.get("name") or "").strip() or "tool"
        self.display_name = display_name or f"mcp:{self.server_label}/{self.tool_name}"
        annotations = self.tool_meta.get("annotations") or {}
        if isinstance(annotations, dict):
            self.read_only = bool(annotations.get("readOnly") or annotations.get("read_only"))
        else:
            self.read_only = False

        self.name = safe_name
        description = str(self.tool_meta.get("description") or "").strip()
        if description:
            self.description = f"{self.display_name} - {description}"
        else:
            self.description = self.display_name
        schema = self.tool_meta.get("inputSchema")
        if schema is None and "input_schema" in self.tool_meta:
            schema = self.tool_meta.get("input_schema")
        self.parameters = _build_parameters_from_schema(schema)

    async def execute(self, input_data: str) -> str:
        args: Any = {}
        if input_data is not None and str(input_data).strip():
            raw = str(input_data).strip()
            try:
                args = json.loads(raw)
            except Exception:
                try:
                    args = ast.literal_eval(raw)
                except Exception:
                    args = raw

        if isinstance(args, dict) and list(args.keys()) == ["arguments"] and isinstance(args["arguments"], dict):
            args = args["arguments"]
        if args is None:
            args = {}
        if not isinstance(args, dict):
            args = {"value": args}

        try:
            return await call_tool(self.server_cfg, self.tool_name, args)
        except Exception as exc:
            return f"MCP tool call failed: {exc}"
