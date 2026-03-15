import asyncio
import asyncio
import json
from typing import Any, Dict, Optional, List

from ..base import Tool, ToolParameter
from ..context import get_tool_context
from app_config import get_app_config
from subagent_runner import (
    prepare_subagent_session,
    execute_subagent_context,
    list_spawnable_profiles,
    run_subagent_task,
    register_subagent_task,
    notify_parent_subagent_started
)


_PENDING_TASKS = set()


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


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "on"):
            return True
        if text in ("false", "0", "no", "n", "off"):
            return False
    return False


def _format_spawnable_profiles(profiles: List[Dict[str, Any]]) -> str:
    if not profiles:
        return "- (none configured)"
    lines: List[str] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id:
            continue
        name = str(profile.get("name") or "").strip()
        description = str(profile.get("description") or "").strip()
        label = f"{profile_id} ({name})" if name and name != profile_id else profile_id
        if description:
            lines.append(f"- {label}: {description}")
        else:
            lines.append(f"- {label}: (no description)")
    return "\n".join(lines) if lines else "- (none configured)"


def _build_subagent_description() -> str:
    base = (
        "Spawn a subagent to run a small task. Returns immediately with a child session id. "
        "Use profile_id to select a spawnable profile; if multiple profiles are spawnable and profile_id is omitted, "
        "the call will fail. Set wait=true to block until the subagent finishes."
    )
    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    spawnable_profiles = list_spawnable_profiles(agent_cfg)
    profile_lines = _format_spawnable_profiles(spawnable_profiles)
    return f"{base}\nSpawnable profiles:\n{profile_lines}"


class SpawnSubagentTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "spawn_subagent"
        self.expose_by_default = False
        self.description = _build_subagent_description()
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
            ),
            ToolParameter(
                name="profile_id",
                type="string",
                description="Optional spawnable profile id to use for the subagent",
                required=False
            ),
            ToolParameter(
                name="wait",
                type="boolean",
                description="Wait for the subagent to complete and return its result",
                required=False
            )
        ]

    def refresh_metadata(self) -> None:
        self.description = _build_subagent_description()

    async def execute(self, input_data: str) -> str:
        try:
            data = _parse_json_input(input_data)
            task = data.get("task") if isinstance(data, dict) else None
            if not task:
                task = input_data
            title = data.get("title") if isinstance(data, dict) else None
            profile_id = None
            wait = False
            if isinstance(data, dict):
                profile_id = data.get("profile_id") or data.get("profile") or data.get("agent_profile")
                wait = _parse_bool(data.get("wait")) if "wait" in data else False

            tool_ctx = get_tool_context()
            parent_session_id = tool_ctx.get("session_id")
            if not parent_session_id:
                return json.dumps(
                    {"status": "error", "error": "Missing parent session"},
                    ensure_ascii=False
                )

            if wait:
                context = prepare_subagent_session(
                    task=str(task),
                    parent_session_id=str(parent_session_id),
                    title=title if isinstance(title, str) else None,
                    profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
                    suppress_parent_notify=True
                )
                await notify_parent_subagent_started(
                    str(parent_session_id),
                    str(context.get("child_session_id", "")),
                    str(context.get("child_title", "Subagent Task"))
                )
                result = await execute_subagent_context(context)
                return json.dumps(result, ensure_ascii=False)

            context = prepare_subagent_session(
                task=str(task),
                parent_session_id=str(parent_session_id),
                title=title if isinstance(title, str) else None,
                profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
                suppress_parent_notify=False
            )
            await notify_parent_subagent_started(
                str(parent_session_id),
                str(context.get("child_session_id", "")),
                str(context.get("child_title", "Subagent Task"))
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

            task_handle = asyncio.create_task(_run_subagent())
            _PENDING_TASKS.add(task_handle)
            register_subagent_task(context.get("child_session_id"), task_handle)
            task_handle.add_done_callback(_on_done)

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
