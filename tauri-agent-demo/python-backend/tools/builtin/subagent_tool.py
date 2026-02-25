import asyncio
import contextvars
import json
from typing import Any, Dict, Optional

from ..base import Tool, ToolParameter
from ..context import get_tool_context
from subagent_runner import prepare_subagent_session, execute_subagent_context


_PENDING_TASKS = set()
_BG_CONTEXT = contextvars.Context()


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
        self.description = "Spawn a subagent to run a small task. Returns immediately with a child session id."
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

            context = prepare_subagent_session(
                task=str(task),
                parent_session_id=str(parent_session_id),
                title=title if isinstance(title, str) else None
            )

            async def _run_subagent():
                await execute_subagent_context(context)

            def _on_done(fut: asyncio.Task) -> None:
                _PENDING_TASKS.discard(fut)
                try:
                    fut.result()
                except asyncio.CancelledError:
                    print("[Subagent] Background task cancelled")
                except Exception as exc:
                    print(f"[Subagent] Background task failed: {exc}")

            def _schedule_background(coro):
                task_handle = asyncio.create_task(coro)
                _PENDING_TASKS.add(task_handle)
                task_handle.add_done_callback(_on_done)
                return task_handle

            # Use a fresh context to avoid inheriting request cancel scopes.
            _BG_CONTEXT.run(_schedule_background, _run_subagent())

            return json.dumps(
                {
                    "status": "started",
                    "child_session_id": context["child_session_id"],
                    "title": context["child_title"]
                },
                ensure_ascii=False
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": f"Subagent tool failed: {exc}"},
                ensure_ascii=False
            )
