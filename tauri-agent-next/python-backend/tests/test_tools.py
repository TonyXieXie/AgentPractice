from __future__ import annotations

from tools.base import Tool, ToolParameter, ToolRegistry, tool_to_openai_function
from tools.context import ToolContext, get_tool_context, reset_tool_context, set_tool_context


class EchoTool(Tool):
    def __init__(self) -> None:
        super().__init__()
        self.name = "echo"
        self.description = "Return the supplied text."
        self.parameters = [
            ToolParameter(name="text", type="string", description="Text to echo")
        ]

    async def execute(self, arguments):
        return arguments["text"]


def test_tool_registry_and_schema() -> None:
    ToolRegistry.clear()
    tool = EchoTool()
    ToolRegistry.register(tool)
    try:
        assert ToolRegistry.get("echo") is tool
        schema = tool_to_openai_function(tool)
        assert schema["function"]["name"] == "echo"
        assert schema["function"]["parameters"]["required"] == ["text"]
    finally:
        ToolRegistry.clear()


def test_tool_context_round_trip() -> None:
    token = set_tool_context(
        ToolContext(
            agent_id="assistant-1",
            run_id="run-1",
            tool_call_id="call-1",
            work_path="E:/repo",
        )
    )
    try:
        context = get_tool_context()
        assert context.agent_id == "assistant-1"
        assert context.run_id == "run-1"
        assert context.tool_call_id == "call-1"
        assert context.work_path == "E:/repo"
    finally:
        reset_tool_context(token)
