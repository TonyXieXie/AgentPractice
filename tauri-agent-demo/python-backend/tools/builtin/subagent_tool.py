import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from ..base import Tool, ToolParameter
from ..context import get_tool_context
from app_config import get_app_config
from models import AgentTaskCancelRequest, AgentTaskCreateRequest, TaskStatus


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


def _build_parallel_subagents_description() -> str:
    from subagent_runner import list_spawnable_profiles

    base = (
        "Run multiple subagents in parallel (Task Center only) and wait for all to finish. "
        "Returns a JSON object with per-task results."
    )
    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    spawnable_profiles = list_spawnable_profiles(agent_cfg)
    profile_lines = _format_spawnable_profiles(spawnable_profiles)
    return f"{base}\nSpawnable profiles:\n{profile_lines}"


def _task_center_enabled() -> bool:
    try:
        tool_ctx = get_tool_context() or {}
        override = tool_ctx.get("use_task_center")
        if isinstance(override, bool):
            return override
    except Exception:
        pass
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
        use_task_center: Optional[bool] = None,
        task_context: Optional[Dict[str, Any]] = None,
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
            if isinstance(use_task_center, bool):
                context["use_task_center"] = use_task_center
            if isinstance(task_context, dict):
                context["task_context"] = dict(task_context)
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
        if isinstance(use_task_center, bool):
            context["use_task_center"] = use_task_center
        if isinstance(task_context, dict):
            context["task_context"] = dict(task_context)
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

            use_task_center_override = tool_ctx.get("use_task_center")
            use_task_center_override = use_task_center_override if isinstance(use_task_center_override, bool) else None
            task_ctx = None
            task_id = tool_ctx.get("task_id")
            instance_id = tool_ctx.get("instance_id")
            if task_id or instance_id:
                task_ctx = {"task_id": task_id, "instance_id": instance_id}
            return await self._execute_legacy(
                task=str(task),
                title=title if isinstance(title, str) else None,
                profile_id=str(profile_id).strip() if isinstance(profile_id, str) and profile_id.strip() else None,
                wait=wait,
                parent_session_id=str(parent_session_id),
                use_task_center=use_task_center_override,
                task_context=task_ctx,
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": f"Subagent tool failed: {exc}"},
                ensure_ascii=False
            )


class SpawnSubagentsParallelTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "spawn_subagents_parallel"
        self.description = _build_parallel_subagents_description()
        self.parameters = [
            ToolParameter(
                name="tasks",
                type="array",
                description="List of subagent tasks to run in parallel",
                required=True,
                items={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task for the subagent to execute"},
                        "title": {"type": "string", "description": "Optional title for this task"},
                        "profile_id": {"type": "string", "description": "Spawnable profile id for this task"},
                    },
                    "required": ["task", "profile_id"],
                    "additionalProperties": False,
                },
            ),
            ToolParameter(
                name="timeout_sec",
                type="number",
                description="Optional per-task timeout in seconds (applied to each task wait)",
                required=False,
            ),
            ToolParameter(
                name="fail_fast",
                type="boolean",
                description="If true, cancel remaining tasks when one task fails",
                required=False,
            ),
            ToolParameter(
                name="emit_progress",
                type="boolean",
                description="If true, emit task_progress events onto the parent task while running",
                required=False,
            ),
        ]

    def refresh_metadata(self) -> None:
        self.description = _build_parallel_subagents_description()

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        tasks_raw = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks_raw, list) or not tasks_raw:
            return json.dumps(
                {"status": "error", "error": "Missing tasks (must be a non-empty array)."},
                ensure_ascii=False,
            )
        if len(tasks_raw) > 12:
            return json.dumps(
                {"status": "error", "error": "Too many tasks (max 12)."},
                ensure_ascii=False,
            )

        tool_ctx = get_tool_context() or {}
        if tool_ctx.get("use_task_center") is not True:
            return json.dumps(
                {"status": "error", "error": "spawn_subagents_parallel requires Task Center (multi-agent) mode."},
                ensure_ascii=False,
            )

        parent_task_id = str(tool_ctx.get("task_id") or "").strip() or None
        if not parent_task_id:
            return json.dumps({"status": "error", "error": "Missing parent task id"}, ensure_ascii=False)
        try:
            from database import db
            parent_task = db.get_agent_task(str(parent_task_id))
        except Exception:
            parent_task = None
        if not parent_task:
            return json.dumps({"status": "error", "error": "Parent task not found"}, ensure_ascii=False)
        if getattr(parent_task, "parent_task_id", None) or (
            getattr(parent_task, "root_task_id", None) and str(parent_task.root_task_id) != str(parent_task.id)
        ):
            return json.dumps(
                {"status": "error", "error": "spawn_subagents_parallel can only be called from a root task"},
                ensure_ascii=False,
            )
        parent_session_id = str(getattr(parent_task, "session_id", "") or "").strip() or None
        if not parent_session_id:
            return json.dumps({"status": "error", "error": "Missing parent session"}, ensure_ascii=False)
        fail_fast = _parse_bool(data.get("fail_fast")) if "fail_fast" in data else False
        emit_progress = _parse_bool(data.get("emit_progress")) if "emit_progress" in data else True

        timeout_sec: Optional[float] = None
        if "timeout_sec" in data and data.get("timeout_sec") is not None:
            try:
                timeout_sec = float(data.get("timeout_sec"))
            except (TypeError, ValueError):
                timeout_sec = None
        if timeout_sec is not None and timeout_sec <= 0:
            timeout_sec = None

        def _emit_parent_progress(message: str, payload: Optional[Dict[str, Any]] = None) -> None:
            if not emit_progress or not parent_task_id:
                return
            try:
                from database import db

                db.append_agent_task_event(
                    task_id=str(parent_task_id),
                    event_type="task_progress",
                    message=message,
                    payload=payload or {},
                )
            except Exception:
                return

        orchestrator = get_task_orchestrator()
        batch_id = hashlib.sha256(f"{time.time()}-{parent_session_id}".encode("utf-8")).hexdigest()[:16]

        created_specs: List[Dict[str, Any]] = []
        try:
            for idx, spec in enumerate(tasks_raw):
                if not isinstance(spec, dict):
                    return json.dumps({"status": "error", "error": f"Task[{idx}] must be an object."}, ensure_ascii=False)

                raw_task = spec.get("task")
                raw_profile = spec.get("profile_id") or spec.get("profile") or spec.get("agent_profile")
                raw_title = spec.get("title")

                task_text = str(raw_task or "").strip()
                profile_id = str(raw_profile or "").strip()
                title = str(raw_title or "").strip() if isinstance(raw_title, str) else None

                if not task_text:
                    return json.dumps({"status": "error", "error": f"Task[{idx}].task is required."}, ensure_ascii=False)
                if not profile_id:
                    return json.dumps({"status": "error", "error": f"Task[{idx}].profile_id is required."}, ensure_ascii=False)

                _emit_parent_progress(
                    f"Planning subtask {idx + 1}/{len(tasks_raw)}: {title or profile_id}",
                    payload={"index": idx, "title": title, "profile_id": profile_id},
                )

                context = prepare_subagent_session(
                    task=task_text,
                    parent_session_id=str(parent_session_id),
                    title=title,
                    profile_id=profile_id,
                    suppress_parent_notify=True,
                )

                metadata = {
                    "kind": "subagent",
                    "origin": "spawn_subagents_parallel",
                    "batch_id": batch_id,
                    "batch_index": idx,
                    "bridge_subagent_events": False,
                    "prepared_subagent_context": _sanitize_context(context),
                }

                stable = json.dumps(
                    {"profile_id": profile_id, "title": title or "", "task": task_text},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                idempotency_key = f"parallel:{parent_task_id or parent_session_id}:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]}"

                create_request = AgentTaskCreateRequest(
                    session_id=str(parent_session_id),
                    title=str(context.get("child_title") or title or f"Subtask {idx + 1}"),
                    input=task_text,
                    target_profile_id=str(context.get("subagent_profile_id") or "") or None,
                    parent_task_id=str(parent_task_id) if parent_task_id else None,
                    source_task_id=str(parent_task_id) if parent_task_id else None,
                    idempotency_key=idempotency_key,
                    metadata=metadata,
                )
                created = await orchestrator.create_task(create_request)
                created_specs.append(
                    {
                        "index": idx,
                        "task_id": created.id,
                        "child_session_id": context.get("child_session_id"),
                        "title": context.get("child_title"),
                        "profile_id": context.get("subagent_profile_id") or profile_id,
                        "task": task_text,
                    }
                )

            _emit_parent_progress(
                f"Spawned {len(created_specs)} subtasks; waiting for completion...",
                payload={"count": len(created_specs), "batch_id": batch_id},
            )

            async def _wait_one(index: int, task_id: str):
                done = await orchestrator.wait_task_terminal(task_id, timeout_sec=timeout_sec)
                return index, done

            waiters = [
                asyncio.create_task(_wait_one(item["index"], item["task_id"]))
                for item in created_specs
            ]
            completed: Dict[int, Dict[str, Any]] = {}
            pending_task_ids: Dict[int, str] = {item["index"]: item["task_id"] for item in created_specs}

            try:
                for fut in asyncio.as_completed(waiters):
                    idx, done = await fut
                    pending_task_ids.pop(idx, None)
                    ok = done.status == TaskStatus.succeeded
                    result = done.result or ""
                    error = None if ok else (done.error_message or "Task failed")
                    entry = {
                        "index": idx,
                        "status": "ok" if ok else "error",
                        "task_id": done.id,
                        "task_status": done.status.value if done.status else None,
                        "child_session_id": done.legacy_child_session_id,
                        "title": next((x.get("title") for x in created_specs if x.get("index") == idx), None),
                        "profile_id": next((x.get("profile_id") for x in created_specs if x.get("index") == idx), None),
                        "result": result,
                        "error": error,
                    }
                    completed[idx] = entry
                    _emit_parent_progress(
                        f"Subtask {idx + 1}/{len(created_specs)} {entry['status']}",
                        payload={"subtask": entry},
                    )

                    if fail_fast and not ok:
                        for other_idx, other_task_id in list(pending_task_ids.items()):
                            try:
                                await orchestrator.cancel_task(
                                    other_task_id,
                                    AgentTaskCancelRequest(reason="Cancelled due to fail_fast", propagate=True),
                                )
                            except Exception:
                                pass
                        break
            except asyncio.CancelledError:
                for item in created_specs:
                    try:
                        await orchestrator.cancel_task(
                            item["task_id"],
                            AgentTaskCancelRequest(reason="Cancelled because parent was cancelled", propagate=True),
                        )
                    except Exception:
                        pass
                raise
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)

            results = [completed.get(i) for i in range(len(tasks_raw))]
            normalized_results = []
            for idx, entry in enumerate(results):
                if entry is None:
                    normalized_results.append(
                        {
                            "index": idx,
                            "status": "error",
                            "task_id": next((x.get("task_id") for x in created_specs if x.get("index") == idx), None),
                            "task_status": None,
                            "child_session_id": next((x.get("child_session_id") for x in created_specs if x.get("index") == idx), None),
                            "title": next((x.get("title") for x in created_specs if x.get("index") == idx), None),
                            "profile_id": next((x.get("profile_id") for x in created_specs if x.get("index") == idx), None),
                            "result": "",
                            "error": "Not completed",
                        }
                    )
                else:
                    normalized_results.append(entry)

            overall_ok = all(item.get("status") == "ok" for item in normalized_results)
            payload = {"status": "ok" if overall_ok else "error", "batch_id": batch_id, "results": normalized_results}
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
