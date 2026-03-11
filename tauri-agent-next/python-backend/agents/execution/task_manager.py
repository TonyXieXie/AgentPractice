from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Dict, TYPE_CHECKING

from agents.execution.message_utils import (
    get_execution_metadata,
    get_session_id,
    get_work_path,
)
from repositories.task_repository import TaskRecord, TaskRepository

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage
    from agents.execution.engine import ExecutionEngine, ExecutionResult


class TaskManager:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository
        self._lock = asyncio.Lock()
        self._agent_locks: Dict[str, asyncio.Lock] = {}
        self._runtime_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._task_ids_by_run: Dict[str, set[str]] = {}

    async def host_message_task(
        self,
        message: "AgentMessage",
        *,
        agent: "AgentBase",
        execution_engine: "ExecutionEngine",
    ) -> "ExecutionResult":
        session_id = get_session_id(message)
        if not session_id:
            raise RuntimeError("hosted task requires session_id")

        agent_lock = await self._get_agent_lock(agent.agent_id)
        async with agent_lock:
            task_record = await self.repository.create(
                session_id=session_id,
                run_id=message.run_id,
                agent_id=agent.agent_id,
                source_message_id=message.id,
                source_message_kind=self._source_message_kind(message),
                topic=message.topic,
                status="running",
                work_path=get_work_path(message),
                metadata=self._build_task_metadata(message),
            )
            await self._register_runtime_task(
                task_record=task_record,
                runtime_task=asyncio.current_task(),
            )
            task_message = self._with_task_id(message, task_id=task_record.id)
            try:
                await agent.update_status(
                    "running",
                    reason=message.topic,
                    metadata={"task_id": task_record.id},
                )
                result = await execution_engine.execute(task_message)
            except asyncio.CancelledError:
                await self.repository.finalize(
                    task_record.id,
                    status="stopped",
                    error_text="task cancelled",
                )
                await agent.update_status(
                    "idle",
                    reason="task.stopped",
                    metadata={
                        "task_id": task_record.id,
                        "task_status": "stopped",
                    },
                )
                raise
            except Exception as exc:
                await self.repository.finalize(
                    task_record.id,
                    status="failed",
                    error_text=str(exc),
                )
                await agent.update_status(
                    "error",
                    reason=message.topic,
                    metadata={
                        "task_id": task_record.id,
                        "task_status": "failed",
                    },
                )
                raise
            else:
                final_status = "completed" if result.ok else "failed"
                error_text = None
                if not result.ok:
                    error_text = str(
                        result.payload.get("error")
                        or result.payload.get("reply")
                        or "task failed"
                    )
                await self.repository.finalize(
                    task_record.id,
                    status=final_status,
                    result=result.payload,
                    error_text=error_text,
                )
                await agent.update_status(
                    "idle" if result.ok else "error",
                    reason=message.topic,
                    metadata={
                        "task_id": task_record.id,
                        "task_status": final_status,
                    },
                )
                return result
            finally:
                await self._unregister_runtime_task(task_record)

    async def run_message_task(
        self,
        message: "AgentMessage",
        *,
        agent: "AgentBase",
        execution_engine: "ExecutionEngine",
    ) -> "ExecutionResult":
        return await self.host_message_task(
            message,
            agent=agent,
            execution_engine=execution_engine,
        )

    async def stop_hosted_tasks(self, run_id: str) -> int:
        runtime_tasks = await self._runtime_tasks_for_run(run_id)
        updated = await self.repository.mark_stopped_by_run(run_id)
        for runtime_task in runtime_tasks:
            if runtime_task is None or runtime_task.done():
                continue
            runtime_task.cancel()
        return updated

    def _build_task_metadata(self, message: "AgentMessage") -> Dict[str, Any]:
        metadata = get_execution_metadata(message)
        requested_target_profile = None
        if isinstance(message.metadata, dict):
            value = message.metadata.get("target_profile")
            if value is not None:
                requested_target_profile = str(value).strip() or None
        metadata.update(
            {
                "sender_id": message.sender_id,
                "target_agent_id": message.target_id,
                "target_profile": message.target_profile or requested_target_profile,
                "correlation_id": message.correlation_id,
                "message_type": message.message_type,
                "object_type": message.object_type,
                "rpc_phase": message.rpc_phase,
                "visibility": message.visibility,
                "level": message.level,
            }
        )
        return metadata

    def _with_task_id(self, message: "AgentMessage", *, task_id: str) -> "AgentMessage":
        metadata = deepcopy(message.metadata) if isinstance(message.metadata, dict) else {}
        metadata["task_id"] = task_id
        return message.model_copy(update={"metadata": metadata})

    async def _get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._agent_locks.get(agent_id)
            if lock is None:
                lock = asyncio.Lock()
                self._agent_locks[agent_id] = lock
            return lock

    async def _register_runtime_task(
        self,
        *,
        task_record: TaskRecord,
        runtime_task: asyncio.Task[Any] | None,
    ) -> None:
        if runtime_task is None:
            return
        async with self._lock:
            self._runtime_tasks[task_record.id] = runtime_task
            if task_record.run_id:
                task_ids = self._task_ids_by_run.setdefault(task_record.run_id, set())
                task_ids.add(task_record.id)

    async def _unregister_runtime_task(self, task_record: TaskRecord) -> None:
        async with self._lock:
            self._runtime_tasks.pop(task_record.id, None)
            if task_record.run_id:
                task_ids = self._task_ids_by_run.get(task_record.run_id)
                if task_ids is not None:
                    task_ids.discard(task_record.id)
                    if not task_ids:
                        self._task_ids_by_run.pop(task_record.run_id, None)

    async def _runtime_tasks_for_run(self, run_id: str) -> list[asyncio.Task[Any]]:
        async with self._lock:
            task_ids = list(self._task_ids_by_run.get(run_id, set()))
            return [
                runtime_task
                for task_id in task_ids
                if (runtime_task := self._runtime_tasks.get(task_id)) is not None
            ]

    def _source_message_kind(self, message: "AgentMessage") -> str:
        if message.message_type == "event":
            return "event"
        if message.rpc_phase == "response":
            return "rpc_response"
        return "rpc_request"
