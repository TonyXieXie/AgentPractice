from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ToolParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    items: Optional[Dict[str, Any]] = None


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot complete successfully."""


class Tool(ABC):
    """Abstract base class for tools exposed to the execution engine."""

    def __init__(self) -> None:
        self.name: str = ""
        self.description: str = ""
        self.parameters: List[ToolParameter] = []

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute the tool with validated JSON arguments."""

    def validate_input(self, arguments: Dict[str, Any]) -> bool:
        if not isinstance(arguments, dict):
            return False
        required = {param.name for param in self.parameters if param.required}
        return required.issubset(arguments.keys())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [param.model_dump() for param in self.parameters],
        }


def _build_tool_parameters_schema(tool: Tool) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in tool.parameters:
        param_type = (
            param.type
            if param.type in {"string", "number", "integer", "boolean", "object", "array"}
            else "string"
        )
        schema: Dict[str, Any] = {
            "type": param_type,
            "description": param.description,
        }
        if param.default is not None:
            schema["default"] = param.default
        if param_type == "array":
            schema["items"] = param.items or {"type": "string"}
        properties[param.name] = schema
        if param.required:
            required.append(param.name)

    payload: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        payload["required"] = required
    return payload


def tool_to_openai_function(tool: Tool) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": _build_tool_parameters_schema(tool),
        },
    }


def tool_to_openai_responses_tool(tool: Tool) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": _build_tool_parameters_schema(tool),
        "strict": True,
    }


class ToolRegistry:
    _tools: Dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool name is required")
        if tool.name in cls._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        cls._tools[tool.name] = tool

    @classmethod
    def unregister(cls, tool_name: str) -> None:
        cls._tools.pop(tool_name, None)

    @classmethod
    def get(cls, tool_name: str) -> Optional[Tool]:
        return cls._tools.get(tool_name)

    @classmethod
    def get_all(cls) -> List[Tool]:
        tools = list(cls._tools.values())
        for tool in tools:
            refresh = getattr(tool, "refresh_metadata", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass
        return tools

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()

    @classmethod
    def list_names(cls) -> List[str]:
        return list(cls._tools.keys())
