from __future__ import annotations

import asyncio
import hashlib
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Set

import httpx

from app_config import get_app_config
from database import db
from models import (
    AgentTask,
    AgentTaskCancelRequest,
    AgentTaskCreateRequest,
    AgentTaskHandoffRequest,
    TaskErrorCode,
    TaskStatus,
)
from agent_instances import get_agent_instance_manager
from subagent_runner import execute_subagent_context, run_subagent_task
from ws_hub import WsHub, get_ws_hub


TERMINAL_STATUSES: Set[TaskStatus] = {
    TaskStatus.succeeded,
    TaskStatus.failed,
    TaskStatus.cancelled,
}

_ALLOWED_TRANSITIONS = {
    TaskStatus.pending: {TaskStatus.running, TaskStatus.cancelled, TaskStatus.blocked},
    TaskStatus.running: {TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled, TaskStatus.blocked},
    TaskStatus.blocked: {TaskStatus.pending, TaskStatus.cancelled, TaskStatus.failed},
    TaskStatus.succeeded: set(),
    TaskStatus.failed: set(),
    TaskStatus.cancelled: set(),
}


@dataclass
class TaskConcurrencyConfig:
    global_limit: int = 8
    per_session_limit: int = 3
    per_instance_limit: int = 1


class TaskOrchestratorError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: TaskErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def is_valid_task_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


class TaskOrchestrator:
    def __init__(self, ws_hub: Optional[WsHub] = None) -> None:
        self.ws_hub = ws_hub or get_ws_hub()
        self.instance_manager = get_agent_instance_manager()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()
        self._running_total = 0
        self._running_by_session: Dict[str, int] = {}
        self._running_by_instance: Dict[str, int] = {}
        self._running_handles: Dict[str, asyncio.Task] = {}

    def _runtime_agent_config(self) -> Dict[str, Any]:
        app_cfg = get_app_config()
        agent_cfg = app_cfg.get("agent") if isinstance(app_cfg, dict) else {}
        return agent_cfg if isinstance(agent_cfg, dict) else {}

    def _loop_max_iterations(self) -> int:
        cfg = self._runtime_agent_config()
        value = cfg.get("max_loop_iterations", 5)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 5
        return max(1, parsed)

    def _concurrency(self) -> TaskConcurrencyConfig:
        cfg = self._runtime_agent_config()
        raw = cfg.get("task_concurrency") if isinstance(cfg, dict) else {}
        if not isinstance(raw, dict):
            return TaskConcurrencyConfig()

        def _to_int(value: Any, fallback: int, minimum: int = 1) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = fallback
            return max(minimum, parsed)

        return TaskConcurrencyConfig(
            global_limit=_to_int(raw.get("global", 8), 8),
            per_session_limit=_to_int(raw.get("per_session", 3), 3),
            per_instance_limit=_to_int(raw.get("per_instance", 1), 1),
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())

    async def stop(self) -> None:
        self._running = False
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._dispatcher_task = None

    async def enqueue_task(self, task_id: str) -> None:
        if not task_id:
            return
        if not self._running:
            await self.start()
        self._queue.put_nowait(task_id)

    async def _dispatcher_loop(self) -> None:
        while self._running:
            task_id = await self._queue.get()
            try:
                await self._dispatch_single(task_id)
            except Exception:
                traceback.print_exc()
            finally:
                self._queue.task_done()

    async def _dispatch_single(self, task_id: str) -> None:
        task = db.get_agent_task(task_id)
        if not task or task.status != TaskStatus.pending:
            return

        reserved = await self._try_reserve_slot(task)
        if not reserved:
            asyncio.create_task(self._requeue_later(task_id, 0.25))
            return

        handle = asyncio.create_task(self._run_task(task.id))
        async with self._lock:
            self._running_handles[task.id] = handle

        def _done_callback(_fut: asyncio.Task) -> None:
            asyncio.create_task(self._release_slot(task.session_id, task.assigned_instance_id or ""))
            asyncio.create_task(self._forget_handle(task.id))

        handle.add_done_callback(_done_callback)

    async def _requeue_later(self, task_id: str, delay_sec: float) -> None:
        await asyncio.sleep(max(0.05, delay_sec))
        await self.enqueue_task(task_id)

    async def _forget_handle(self, task_id: str) -> None:
        async with self._lock:
            self._running_handles.pop(task_id, None)

    async def _try_reserve_slot(self, task: AgentTask) -> bool:
        limits = self._concurrency()
        session_key = task.session_id
        instance_key = task.assigned_instance_id or ""
        async with self._lock:
            session_running = self._running_by_session.get(session_key, 0)
            instance_running = self._running_by_instance.get(instance_key, 0) if instance_key else 0
            if self._running_total >= limits.global_limit:
                return False
            if session_running >= limits.per_session_limit:
                return False
            if instance_key and instance_running >= limits.per_instance_limit:
                return False
            self._running_total += 1
            self._running_by_session[session_key] = session_running + 1
            if instance_key:
                self._running_by_instance[instance_key] = instance_running + 1
            return True

    async def _release_slot(self, session_id: str, instance_id: str) -> None:
        async with self._lock:
            self._running_total = max(0, self._running_total - 1)
            if session_id:
                next_session = max(0, self._running_by_session.get(session_id, 0) - 1)
                if next_session:
                    self._running_by_session[session_id] = next_session
                else:
                    self._running_by_session.pop(session_id, None)
            if instance_id:
                next_instance = max(0, self._running_by_instance.get(instance_id, 0) - 1)
                if next_instance:
                    self._running_by_instance[instance_id] = next_instance
                else:
                    self._running_by_instance.pop(instance_id, None)

    def _ensure_transition(self, task: AgentTask, target: TaskStatus) -> None:
        if not is_valid_task_transition(task.status, target):
            raise TaskOrchestratorError(
                status_code=409,
                code=TaskErrorCode.invalid_transition,
                message=f"Invalid task transition: {task.status.value} -> {target.value}",
                details={"task_id": task.id, "from": task.status.value, "to": target.value},
            )

    async def _run_task(self, task_id: str) -> None:
        task = db.get_agent_task(task_id)
        if not task or task.status != TaskStatus.pending:
            return

        try:
            self._ensure_transition(task, TaskStatus.running)
            task = db.update_agent_task(task.id, status=TaskStatus.running)
            if not task:
                return
            await self._emit_event(
                task,
                event_type="task_started",
                status=TaskStatus.running,
                message=f"Task started on instance {task.assigned_instance_id or 'auto'}",
                payload={"instance_id": task.assigned_instance_id, "task_id": task.id},
            )

            result = await self._execute_task(task)

            task = db.update_agent_task(
                task.id,
                status=TaskStatus.succeeded,
                result=result.get("result") or "",
                error_code=None,
                error_message=None,
                legacy_child_session_id=result.get("child_session_id")
            )
            if not task:
                return
            await self._emit_event(
                task,
                event_type="task_completed",
                status=TaskStatus.succeeded,
                message="Task completed",
                payload=result,
            )
        except asyncio.CancelledError:
            latest = db.get_agent_task(task_id)
            if latest and latest.status not in TERMINAL_STATUSES:
                latest = db.update_agent_task(
                    latest.id,
                    status=TaskStatus.cancelled,
                    error_code=TaskErrorCode.cancelled_by_parent,
                    error_message="Task cancelled"
                )
                if latest:
                    await self._emit_event(
                        latest,
                        event_type="task_cancelled",
                        status=TaskStatus.cancelled,
                        message="Task cancelled",
                        error_code=TaskErrorCode.cancelled_by_parent,
                        error_message="Task cancelled",
                    )
            raise
        except TaskOrchestratorError as exc:
            latest = db.get_agent_task(task_id)
            if latest and latest.status not in TERMINAL_STATUSES:
                latest = db.update_agent_task(
                    latest.id,
                    status=TaskStatus.failed,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                if latest:
                    await self._emit_event(
                        latest,
                        event_type="task_failed",
                        status=TaskStatus.failed,
                        message=exc.message,
                        error_code=exc.code,
                        error_message=exc.message,
                        payload={"details": exc.details},
                    )
        except Exception as exc:
            latest = db.get_agent_task(task_id)
            if not latest or latest.status in TERMINAL_STATUSES:
                return

            transient = self._is_transient_error(exc)
            if transient and latest.retry_count < max(0, latest.max_retries):
                next_retry = latest.retry_count + 1
                latest = db.update_agent_task(
                    latest.id,
                    status=TaskStatus.pending,
                    retry_count=next_retry,
                    error_code=None,
                    error_message=None,
                )
                if latest:
                    await self._emit_event(
                        latest,
                        event_type="task_progress",
                        status=TaskStatus.pending,
                        message=f"Transient error; retry {next_retry}/{latest.max_retries}",
                        payload={"retry_count": next_retry, "error": str(exc)},
                    )
                    await self.enqueue_task(latest.id)
                return

            code = TaskErrorCode.transient_retry_exhausted if transient else TaskErrorCode.internal_error
            latest = db.update_agent_task(
                latest.id,
                status=TaskStatus.failed,
                error_code=code,
                error_message=str(exc),
            )
            if latest:
                await self._emit_event(
                    latest,
                    event_type="task_failed",
                    status=TaskStatus.failed,
                    message=str(exc),
                    error_code=code,
                    error_message=str(exc),
                    payload={"traceback": traceback.format_exc()},
                )

    def _is_transient_error(self, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = getattr(exc.response, "status_code", None)
            return status in (408, 409, 425, 429, 500, 502, 503, 504)
        text = str(exc).lower()
        markers = ("timeout", "temporar", "connection reset", "econn", "rate limit")
        return any(marker in text for marker in markers)

    async def _execute_task(self, task: AgentTask) -> Dict[str, Any]:
        instance = db.get_agent_instance(task.assigned_instance_id) if task.assigned_instance_id else None
        profile_id = task.target_profile_id or (instance.profile_id if instance else None)
        metadata = task.metadata or {}
        prepared_context = metadata.get("prepared_subagent_context") if isinstance(metadata, dict) else None

        result_payload: Dict[str, Any]
        if isinstance(prepared_context, dict):
            context = dict(prepared_context)
            exec_result = await execute_subagent_context(context)
            if str(exec_result.get("status")) not in ("ok", "success"):
                raise RuntimeError(str(exec_result.get("error") or exec_result.get("result") or "Subagent execution failed"))
            result_payload = {
                "result": str(exec_result.get("result") or ""),
                "child_session_id": exec_result.get("child_session_id"),
                "title": exec_result.get("title"),
            }
        else:
            exec_result = await run_subagent_task(
                task=task.input,
                parent_session_id=task.session_id,
                title=task.title,
                profile_id=profile_id,
                suppress_parent_notify=True,
            )
            if str(exec_result.get("status")) not in ("ok", "success"):
                raise RuntimeError(str(exec_result.get("error") or exec_result.get("result") or "Task execution failed"))
            result_payload = {
                "result": str(exec_result.get("result") or ""),
                "child_session_id": exec_result.get("child_session_id"),
                "title": exec_result.get("title") or task.title,
            }

        child_session_id = result_payload.get("child_session_id")
        if child_session_id:
            snapshot = db.get_latest_file_snapshot_for_session(str(child_session_id))
            checksum = hashlib.sha256(str(result_payload.get("result") or "").encode("utf-8")).hexdigest()
            db.save_agent_artifact(
                task_id=task.id,
                session_id=task.session_id,
                artifact_type="subagent_result",
                path=str(child_session_id),
                tree_hash=(snapshot or {}).get("tree_hash") if snapshot else None,
                checksum=checksum,
                metadata={
                    "child_session_id": child_session_id,
                    "title": result_payload.get("title"),
                },
            )

        return result_payload

    async def _emit_event(
        self,
        task: AgentTask,
        *,
        event_type: str,
        status: Optional[TaskStatus] = None,
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        error_code: Optional[TaskErrorCode] = None,
        error_message: Optional[str] = None,
    ) -> None:
        event = db.append_agent_task_event(
            task_id=task.id,
            event_type=event_type,
            status=status,
            message=message,
            payload=payload or {},
            error_code=error_code,
            error_message=error_message,
        )
        ws_payload = {
            "type": event.event_type,
            "session_id": task.session_id,
            "task_id": task.id,
            "seq": event.seq,
            "status": event.status.value if event.status else None,
            "message": event.message,
            "payload": event.payload,
            "error_code": event.error_code.value if event.error_code else None,
            "error_message": event.error_message,
            "timestamp": event.created_at,
            "instance_id": task.assigned_instance_id,
        }
        await self.ws_hub.emit(task.session_id, ws_payload)

        # Optional compatibility bridge for legacy event consumers.
        metadata = task.metadata or {}
        if not metadata.get("bridge_subagent_events"):
            return
        child_session_id = None
        if isinstance(payload, dict):
            child_session_id = payload.get("child_session_id")
        prepared_ctx = metadata.get("prepared_subagent_context") if isinstance(metadata, dict) else None
        if isinstance(prepared_ctx, dict):
            child_session_id = child_session_id or prepared_ctx.get("child_session_id")
        child_session_id = child_session_id or task.legacy_child_session_id

        if event_type == "task_started" and child_session_id:
            await self.ws_hub.emit(
                task.session_id,
                {
                    "type": "subagent_started",
                    "session_id": task.session_id,
                    "child_session_id": child_session_id,
                    "child_title": task.title or "Subagent Task",
                },
            )
        elif event_type in ("task_completed", "task_failed", "task_cancelled") and child_session_id:
            status_label = "ok" if event_type == "task_completed" else "error"
            now = datetime.now().isoformat()
            await self.ws_hub.emit(
                task.session_id,
                {
                    "type": "subagent_done",
                    "session_id": task.session_id,
                    "message": {
                        "id": 0,
                        "session_id": task.session_id,
                        "role": "assistant",
                        "content": str((payload or {}).get("result") or message or ""),
                        "timestamp": now,
                        "metadata": {
                            "subagent_done": True,
                            "child_session_id": child_session_id,
                            "child_title": task.title or "Subagent Task",
                            "status": status_label,
                        },
                    },
                    "child_session_id": child_session_id,
                    "status": status_label,
                },
            )

    async def create_task(self, request: AgentTaskCreateRequest) -> AgentTask:
        if not request.session_id:
            raise TaskOrchestratorError(
                status_code=400,
                code=TaskErrorCode.invalid_request,
                message="session_id is required",
            )
        if not str(request.input or "").strip():
            raise TaskOrchestratorError(
                status_code=400,
                code=TaskErrorCode.invalid_request,
                message="input is required",
            )

        if request.idempotency_key:
            existing = db.get_agent_task_by_idempotency(request.session_id, request.idempotency_key)
            if existing:
                return existing

        session = db.get_session(request.session_id, include_count=False)
        if not session:
            raise TaskOrchestratorError(
                status_code=404,
                code=TaskErrorCode.invalid_request,
                message="session not found",
                details={"session_id": request.session_id},
            )

        agent_cfg = self._runtime_agent_config()
        self.instance_manager.ensure_session_instances(request.session_id, agent_cfg)

        instance = self.instance_manager.resolve_instance(
            session_id=request.session_id,
            target_instance_id=request.target_instance_id,
            target_profile_id=request.target_profile_id,
            required_abilities=request.required_abilities,
            agent_config=agent_cfg,
        )
        if request.target_instance_id and not instance:
            raise TaskOrchestratorError(
                status_code=404,
                code=TaskErrorCode.instance_not_found,
                message="target instance not found",
                details={"target_instance_id": request.target_instance_id},
            )
        if not instance:
            raise TaskOrchestratorError(
                status_code=404,
                code=TaskErrorCode.route_not_resolved,
                message="No matching agent instance for task",
                details={
                    "target_profile_id": request.target_profile_id,
                    "required_abilities": request.required_abilities,
                },
            )

        parent_task = db.get_agent_task(request.parent_task_id) if request.parent_task_id else None
        root_task_id = request.root_task_id
        if parent_task and not root_task_id:
            root_task_id = parent_task.root_task_id or parent_task.id

        loop_group_id = request.loop_group_id or (parent_task.loop_group_id if parent_task else None)
        if parent_task and not loop_group_id:
            loop_group_id = parent_task.id

        if request.loop_iteration is not None:
            loop_iteration = int(request.loop_iteration)
        elif parent_task and loop_group_id:
            loop_iteration = int(parent_task.loop_iteration) + 1
        else:
            loop_iteration = 0

        max_loop_iterations = self._loop_max_iterations()
        if loop_group_id and loop_iteration > max_loop_iterations:
            raise TaskOrchestratorError(
                status_code=409,
                code=TaskErrorCode.loop_iteration_exceeded,
                message="Loop iteration exceeded",
                details={
                    "loop_group_id": loop_group_id,
                    "loop_iteration": loop_iteration,
                    "max_loop_iterations": max_loop_iterations,
                },
            )

        max_retries = request.max_retries if request.max_retries is not None else 2
        try:
            max_retries = int(max_retries)
        except (TypeError, ValueError):
            max_retries = 2
        max_retries = max(0, max_retries)

        created = db.create_agent_task(
            session_id=request.session_id,
            title=request.title,
            input_text=str(request.input),
            status=TaskStatus.pending,
            assigned_instance_id=instance.id,
            target_profile_id=instance.profile_id,
            required_abilities=request.required_abilities,
            parent_task_id=request.parent_task_id,
            root_task_id=root_task_id,
            source_task_id=request.source_task_id,
            loop_group_id=loop_group_id,
            loop_iteration=loop_iteration,
            max_retries=max_retries,
            retry_count=0,
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
            initial_event={
                "event_type": "task_progress",
                "status": TaskStatus.pending,
                "message": "Task created",
                "payload": {
                    "assigned_instance_id": instance.id,
                    "target_profile_id": instance.profile_id,
                },
            },
        )

        if not created.root_task_id:
            created = db.update_agent_task(created.id, root_task_id=created.id) or created

        if request.parent_task_id:
            db.add_agent_task_edge(request.parent_task_id, created.id, "handoff")

        await self.enqueue_task(created.id)
        return created

    async def handoff_task(self, from_task_id: str, request: AgentTaskHandoffRequest) -> AgentTask:
        source = db.get_agent_task(from_task_id)
        if not source:
            raise TaskOrchestratorError(
                status_code=404,
                code=TaskErrorCode.task_not_found,
                message="source task not found",
                details={"task_id": from_task_id},
            )

        payload = request.input if request.input is not None else (source.result or source.input)
        create_req = AgentTaskCreateRequest(
            session_id=source.session_id,
            title=request.title or source.title,
            input=str(payload or ""),
            target_instance_id=request.target_instance_id,
            target_profile_id=request.target_profile_id,
            required_abilities=request.required_abilities,
            parent_task_id=source.id,
            root_task_id=source.root_task_id or source.id,
            source_task_id=source.id,
            loop_group_id=request.loop_group_id or source.loop_group_id or source.id,
            loop_iteration=request.loop_iteration,
            max_retries=source.max_retries,
            metadata=request.metadata,
        )
        child = await self.create_task(create_req)

        db.add_agent_task_edge(source.id, child.id, "handoff", metadata={"from": source.id, "to": child.id})
        await self._emit_event(
            source,
            event_type="task_handoff",
            status=source.status,
            message="Task handed off",
            payload={"to_task_id": child.id, "to_instance_id": child.assigned_instance_id},
        )
        return child

    async def cancel_task(self, task_id: str, request: Optional[AgentTaskCancelRequest] = None) -> AgentTask:
        req = request or AgentTaskCancelRequest()
        task = db.get_agent_task(task_id)
        if not task:
            raise TaskOrchestratorError(
                status_code=404,
                code=TaskErrorCode.task_not_found,
                message="task not found",
                details={"task_id": task_id},
            )
        if task.status in TERMINAL_STATUSES:
            return task

        task = db.update_agent_task(
            task.id,
            status=TaskStatus.cancelled,
            error_code=TaskErrorCode.cancelled_by_parent,
            error_message=req.reason or "Task cancelled",
        ) or task
        await self._emit_event(
            task,
            event_type="task_cancelled",
            status=TaskStatus.cancelled,
            message=req.reason or "Task cancelled",
            error_code=TaskErrorCode.cancelled_by_parent,
            error_message=req.reason or "Task cancelled",
        )

        handle = None
        async with self._lock:
            handle = self._running_handles.get(task.id)
        if handle and not handle.done():
            handle.cancel()

        if req.propagate:
            await self._cancel_downstream_unstarted(task.id)
        refreshed = db.get_agent_task(task.id)
        return refreshed or task

    async def _cancel_downstream_unstarted(self, from_task_id: str) -> None:
        child_ids = db.list_downstream_task_ids(from_task_id)
        for child_id in child_ids:
            child = db.get_agent_task(child_id)
            if not child:
                continue
            if child.status in (TaskStatus.pending, TaskStatus.blocked):
                child = db.update_agent_task(
                    child.id,
                    status=TaskStatus.cancelled,
                    error_code=TaskErrorCode.cancelled_by_parent,
                    error_message="Cancelled because upstream task was cancelled",
                ) or child
                await self._emit_event(
                    child,
                    event_type="task_cancelled",
                    status=TaskStatus.cancelled,
                    message="Cancelled because upstream task was cancelled",
                    error_code=TaskErrorCode.cancelled_by_parent,
                    error_message="Cancelled because upstream task was cancelled",
                )
                await self._cancel_downstream_unstarted(child.id)

    async def wait_task_terminal(self, task_id: str, timeout_sec: Optional[float] = None) -> AgentTask:
        deadline = None
        if timeout_sec is not None:
            deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout_sec))

        while True:
            task = db.get_agent_task(task_id)
            if not task:
                raise TaskOrchestratorError(
                    status_code=404,
                    code=TaskErrorCode.task_not_found,
                    message="task not found",
                    details={"task_id": task_id},
                )
            if task.status in TERMINAL_STATUSES:
                return task
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TaskOrchestratorError(
                    status_code=408,
                    code=TaskErrorCode.internal_error,
                    message="timeout waiting for task completion",
                    details={"task_id": task_id},
                )
            await asyncio.sleep(0.2)


_TASK_ORCHESTRATOR = TaskOrchestrator()


def get_task_orchestrator() -> TaskOrchestrator:
    return _TASK_ORCHESTRATOR
