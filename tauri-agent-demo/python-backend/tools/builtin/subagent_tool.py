import json
from typing import Any, Dict, Optional

from ..base import Tool, ToolParameter
from ..context import get_tool_context
from subagent_runner import run_subagent_task


def _parse_json_input(input_data: str) -> Dict[str, Any]:
    if not input_data:
        return {}
    text = str(input_data).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class SpawnSubagentTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "spawn_subagent"
        self.description = "Spawn a subagent to run a small task and return the result."
        self.parameters = [
            ToolParameter(
                name="task",
                type="string",
                description="Task for the subagent to execute",
                required=True
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Optional title for the subagent session",
                required=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        try:
            data = _parse_json_input(input_data)
            task = data.get("task") if isinstance(data, dict) else None
            if not task:
                task = input_data
            title = data.get("title") if isinstance(data, dict) else None

            tool_ctx = get_tool_context()
            parent_session_id = tool_ctx.get("session_id")
            if not parent_session_id:
                return json.dumps(
                    {"status": "error", "error": "Missing parent session"},
                    ensure_ascii=False
                )

            result = await run_subagent_task(
                task=str(task),
                parent_session_id=str(parent_session_id),
                title=title if isinstance(title, str) else None
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": f"Subagent tool failed: {exc}"},
                ensure_ascii=False
            )
