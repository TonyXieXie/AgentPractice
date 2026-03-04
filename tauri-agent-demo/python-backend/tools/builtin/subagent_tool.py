import asyncio
import json
from typing import Any, Dict, List

from ..base import Tool, ToolParameter
from ..context import get_tool_context
from app_config import get_app_config
from models import AgentTaskCreateRequest, TaskStatus


_PENDING_TASKS = set()


def prepare_subagent_session(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    from subagent_runner import prepare_subagent_session as _prepare_subagent_session

    return _prepare_subagent_session(*args, **kwargs)


async def notify_parent_subagent_started(*args: Any, **kwargs: Any) -> None:
    from subagent_runner import notify_parent_subagent_started as _notify_parent_subagent_started

    await _notify_parent_subagent_started(*args, **kwargs)


def get_task_orchestrator() -> Any:
    from task_orchestrator import get_task_orchestrator as _get_task_orchestrator

    return _get_task_orchestrator()


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
    from subagent_runner import list_spawnable_profiles

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


def _task_center_enabled() -> bool:
    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    return bool(agent_cfg.get("task_center_enabled", False))


def _sanitize_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    context = dict(raw)
    context.pop("child_session", None)
    # Ensure the payload can be serialized into task metadata.
    for key in list(context.keys()):
        value = context.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            continue
        if isinstance(value, dict):
            try:
                json.dumps(value, ensure_ascii=False)
            except Exception:
                context[key] = str(value)
            continue
        context[key] = str(value)
    return context


class SpawnSubagentTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "spawn_subagent"
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

    async def _execute_legacy(
        self,
        *,
        task: str,
        title: str,
        profile_id: str,
        wait: bool,
        parent_session_id: str,
    ) -> str:
        from subagent_runner import (
            execute_subagent_context,
            register_subagent_task,
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

    async def _execute_task_center(
        self,
        *,
        task: str,
        title: str,
        profile_id: str,
        wait: bool,
        parent_session_id: str,
        parent_task_id: str,
    ) -> str:
        context = prepare_subagent_session(
            task=str(task),
            parent_session_id=str(parent_session_id),
            title=title if isinstance(title, str) else None,
            profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
            suppress_parent_notify=True,
        )

        metadata = {
            "kind": "subagent",
            "bridge_subagent_events": not wait,
            "prepared_subagent_context": _sanitize_context(context),
        }

        create_request = AgentTaskCreateRequest(
            session_id=str(parent_session_id),
            title=str(context.get("child_title") or "Subagent Task"),
            input=str(task),
            target_profile_id=str(context.get("subagent_profile_id") or "") or None,
            parent_task_id=str(parent_task_id) if parent_task_id else None,
            source_task_id=str(parent_task_id) if parent_task_id else None,
            metadata=metadata,
        )

        orchestrator = get_task_orchestrator()
        created = await orchestrator.create_task(create_request)

        if wait:
            done = await orchestrator.wait_task_terminal(created.id)
            if done.status == TaskStatus.succeeded:
                status = "ok"
                result = done.result or ""
                error = None
            elif done.status == TaskStatus.cancelled:
                status = "error"
                result = done.error_message or "Task cancelled"
                error = done.error_message or "Task cancelled"
            else:
                status = "error"
                result = done.error_message or "Task failed"
                error = done.error_message or "Task failed"
            return json.dumps(
                {
                    "status": status,
                    "task_id": done.id,
                    "child_session_id": done.legacy_child_session_id or context.get("child_session_id"),
                    "title": context.get("child_title"),
                    "result": result,
                    "error": error,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "status": "started",
                "task_id": created.id,
                "child_session_id": context.get("child_session_id"),
                "title": context.get("child_title"),
            },
            ensure_ascii=False,
        )

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

            if _task_center_enabled():
                try:
                    return await self._execute_task_center(
                        task=str(task),
                        title=title if isinstance(title, str) else None,
                        profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
                        wait=wait,
                        parent_session_id=str(parent_session_id),
                        parent_task_id=str(tool_ctx.get("task_id") or "").strip(),
                    )
                except Exception as exc:
                    code = getattr(getattr(exc, "code", None), "value", None) or str(getattr(exc, "code", "internal_error"))
                    details = getattr(exc, "details", {}) or {}
                    return json.dumps(
                        {
                            "status": "error",
                            "error": str(getattr(exc, "message", str(exc))),
                            "code": code,
                            "details": details,
                        },
                        ensure_ascii=False,
                    )

            return await self._execute_legacy(
                task=str(task),
                title=title if isinstance(title, str) else None,
                profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
                wait=wait,
                parent_session_id=str(parent_session_id),
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": f"Subagent tool failed: {exc}"},
                ensure_ascii=False
            )
