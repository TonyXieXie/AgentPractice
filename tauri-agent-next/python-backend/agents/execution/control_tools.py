from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from agents.execution.directives import DIRECTIVE_KINDS
from tools.base import Tool, ToolParameter


class ControlTool(Tool):
    directive_kind: str = ""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: List[ToolParameter],
    ) -> None:
        super().__init__()
        self.name = name
        self.directive_kind = name
        self.description = description
        self.parameters = parameters

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        return {
            "__directive__": "execution",
            "kind": self.directive_kind,
            "args": self._normalize_arguments(arguments),
        }

    def _normalize_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return dict(arguments or {})


class RoutedSendTool(ControlTool):
    def validate_input(self, arguments: Dict[str, Any]) -> bool:
        if not super().validate_input(arguments):
            return False
        target_agent_id = str(arguments.get("target_agent_id") or "").strip()
        target_profile = str(arguments.get("target_profile") or "").strip()
        return bool(target_agent_id or target_profile)


class SendRpcResponseTool(ControlTool):
    def validate_input(self, arguments: Dict[str, Any]) -> bool:
        if not super().validate_input(arguments):
            return False
        reply_to_message_id = str(arguments.get("reply_to_message_id") or "").strip()
        return bool(reply_to_message_id)


def build_control_tools(
    *,
    allowed_directive_kinds: Optional[Iterable[str]] = None,
) -> List[Tool]:
    allowed = {
        str(kind or "").strip()
        for kind in (
            allowed_directive_kinds
            if allowed_directive_kinds is not None
            else DIRECTIVE_KINDS
        )
    }
    tools = [
        RoutedSendTool(
            name="send_rpc_request",
            description="Send a target RPC request to another agent and then wait for follow-up messages.",
            parameters=[
                ToolParameter(name="topic", type="string", description="RPC topic"),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="RPC payload object",
                ),
                ToolParameter(
                    name="target_agent_id",
                    type="string",
                    description="Concrete target agent id",
                    required=False,
                ),
                ToolParameter(
                    name="target_profile",
                    type="string",
                    description="Target agent profile id when routing by profile",
                    required=False,
                ),
                ToolParameter(
                    name="timeout_ms",
                    type="integer",
                    description="Request timeout in milliseconds",
                    required=False,
                ),
                ToolParameter(
                    name="visibility",
                    type="string",
                    description="Message visibility",
                    required=False,
                ),
                ToolParameter(
                    name="level",
                    type="string",
                    description="Message severity level",
                    required=False,
                ),
            ],
        ),
        SendRpcResponseTool(
            name="send_rpc_response",
            description="Send an RPC response for a previous RPC request message.",
            parameters=[
                ToolParameter(
                    name="reply_to_message_id",
                    type="string",
                    description="Original RPC request message id",
                ),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="Response payload object",
                ),
                ToolParameter(
                    name="ok",
                    type="boolean",
                    description="Whether the RPC completed successfully",
                ),
                ToolParameter(
                    name="visibility",
                    type="string",
                    description="Message visibility",
                    required=False,
                ),
                ToolParameter(
                    name="level",
                    type="string",
                    description="Message severity level",
                    required=False,
                ),
            ],
        ),
        RoutedSendTool(
            name="send_event",
            description="Send a target event to another agent.",
            parameters=[
                ToolParameter(name="topic", type="string", description="Event topic"),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="Event payload object",
                ),
                ToolParameter(
                    name="target_agent_id",
                    type="string",
                    description="Concrete target agent id",
                    required=False,
                ),
                ToolParameter(
                    name="target_profile",
                    type="string",
                    description="Target agent profile id when routing by profile",
                    required=False,
                ),
                ToolParameter(
                    name="visibility",
                    type="string",
                    description="Message visibility",
                    required=False,
                ),
                ToolParameter(
                    name="level",
                    type="string",
                    description="Message severity level",
                    required=False,
                ),
            ],
        ),
        ControlTool(
            name="broadcast_event",
            description="Broadcast an event to subscribed agents in the same run or session.",
            parameters=[
                ToolParameter(name="topic", type="string", description="Event topic"),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="Event payload object",
                ),
                ToolParameter(
                    name="visibility",
                    type="string",
                    description="Message visibility",
                    required=False,
                ),
                ToolParameter(
                    name="level",
                    type="string",
                    description="Message severity level",
                    required=False,
                ),
            ],
        ),
        ControlTool(
            name="finish_run",
            description="Mark the current run as completed with a final reply.",
            parameters=[
                ToolParameter(name="reply", type="string", description="Final user-visible reply"),
                ToolParameter(
                    name="status",
                    type="string",
                    description="Final run status",
                    required=False,
                    default="completed",
                ),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="Additional finish payload",
                    required=False,
                ),
            ],
        ),
        ControlTool(
            name="fail_run",
            description="Mark the current run as failed with an error message.",
            parameters=[
                ToolParameter(name="error", type="string", description="Failure reason"),
                ToolParameter(
                    name="payload",
                    type="object",
                    description="Additional failure payload",
                    required=False,
                ),
            ],
        ),
        ControlTool(
            name="stop_run",
            description="Stop the current run immediately.",
            parameters=[
                ToolParameter(
                    name="reason",
                    type="string",
                    description="Optional stop reason",
                    required=False,
                ),
            ],
        ),
    ]
    return [tool for tool in tools if tool.directive_kind in allowed]
