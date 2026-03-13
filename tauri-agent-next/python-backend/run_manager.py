from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4

from agents.assistant import AssistantAgent
from agents.base import AgentBase
from agents.center import AgentCenter
from agents.execution import TaskManager
from agents.roster_manager import AgentRosterManager
from models import StopRunResponse
from observation.center import ObservationCenter


@dataclass
class ActiveRun:
    run_id: str
    session_id: str
    controller_agent_id: str
    runtime_agents: Dict[str, AgentBase]
    entry_assistant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    root_task: Optional[asyncio.Task[None]] = None

    @property
    def entry_assistant(self) -> AssistantAgent:
        if not self.entry_assistant_id:
            raise RuntimeError("entry assistant is not available for this run")
        agent = self.runtime_agents[self.entry_assistant_id]
        if not isinstance(agent, AssistantAgent):
            raise RuntimeError(f"runtime agent {self.entry_assistant_id} is not AssistantAgent")
        return agent


class SessionNotFoundError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"session not found: {session_id}")
        self.session_id = session_id


class SessionBusyError(RuntimeError):
    def __init__(self, session_id: str, active_run_id: str) -> None:
        super().__init__(f"session busy: {session_id} (active_run_id={active_run_id})")
        self.session_id = session_id
        self.active_run_id = active_run_id


class RunManager:
    def __init__(
        self,
        *,
        agent_center: AgentCenter,
        observation_center: ObservationCenter,
        agent_roster_manager: AgentRosterManager,
        task_manager: TaskManager,
    ) -> None:
        self.agent_center = agent_center
        self.observation_center = observation_center
        self.agent_roster_manager = agent_roster_manager
        self.task_manager = task_manager
        self._active_runs: Dict[str, ActiveRun] = {}
        self._busy_sessions: Dict[str, str] = {}
        self._known_run_sessions: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def open_run(
        self,
        session_id: str,
        controller_agent_id: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        entry_assistant_id: Optional[str] = None,
    ) -> ActiveRun:
        run_id = uuid4().hex
        async with self._lock:
            active_run_id = self._busy_sessions.get(session_id)
            if active_run_id:
                raise SessionBusyError(session_id, active_run_id)
            self._busy_sessions[session_id] = run_id
            self._known_run_sessions[run_id] = session_id

        try:
            runtime_agents = await self.agent_roster_manager.hydrate_run_roster(
                session_id=session_id,
                run_id=run_id,
            )
            if entry_assistant_id and entry_assistant_id not in runtime_agents:
                raise RuntimeError(
                    f"Primary assistant runtime agent missing: {entry_assistant_id}"
                )
            active_run = ActiveRun(
                run_id=run_id,
                session_id=session_id,
                controller_agent_id=controller_agent_id,
                runtime_agents=runtime_agents,
                entry_assistant_id=entry_assistant_id,
                metadata=dict(metadata or {}),
            )
            async with self._lock:
                self._active_runs[run_id] = active_run

            await self._append_run_lifecycle_fact(
                active_run,
                topic="run.started",
                status="running",
                reply="",
                message_id=None,
            )
            return active_run
        except Exception:
            await self.agent_roster_manager.teardown_run(run_id)
            async with self._lock:
                self._active_runs.pop(run_id, None)
                if self._busy_sessions.get(session_id) == run_id:
                    self._busy_sessions.pop(session_id, None)
            raise

    async def attach_root_task(
        self,
        run_id: str,
        root_task: asyncio.Task[None],
    ) -> Optional[ActiveRun]:
        async with self._lock:
            active_run = self._active_runs.get(run_id)
            if active_run is None:
                return None
            active_run.root_task = root_task
            return active_run

    async def finish_run(
        self,
        run_id: str,
        result_payload: Dict[str, Any],
        message_id: Optional[str] = None,
    ) -> None:
        active_run = await self.get_active_run(run_id)
        if active_run is None:
            return
        status = str(result_payload.get("status") or "completed")
        reply = str(
            result_payload.get("reply")
            or result_payload.get("error")
            or result_payload.get("result")
            or ""
        )
        await self._append_run_lifecycle_fact(
            active_run,
            topic="run.finished",
            status=status,
            reply=reply,
            message_id=message_id,
            error=str(result_payload.get("error") or "") if status == "error" else None,
            strategy=str(
                result_payload.get("strategy")
                or active_run.metadata.get("strategy")
                or "simple"
            ),
        )
        await self._finalize_run(active_run)

    async def fail_run(
        self,
        run_id: str,
        error_text: str,
        *,
        message_id: Optional[str] = None,
        result_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        active_run = await self.get_active_run(run_id)
        if active_run is None:
            return
        payload = dict(result_payload or {})
        await self._append_run_lifecycle_fact(
            active_run,
            topic="run.finished",
            status="error",
            reply=str(error_text),
            message_id=message_id,
            error=str(error_text),
            strategy=str(payload.get("strategy") or active_run.metadata.get("strategy") or "simple"),
        )
        await self._finalize_run(active_run)

    async def stop_run(self, run_id: str) -> Optional[StopRunResponse]:
        active_run = await self.get_active_run(run_id)
        if active_run is None:
            session_id = await self.lookup_session_id(run_id)
            if not session_id:
                return None
            status = await self.observation_center.fact_query_service.get_latest_run_status(run_id)
            if not status:
                return None
            return StopRunResponse(run_id=run_id, status=status)

        await self.task_manager.stop_hosted_tasks(run_id)
        if active_run.root_task is not None and not active_run.root_task.done():
            active_run.root_task.cancel()

        await self._append_run_lifecycle_fact(
            active_run,
            topic="run.finished",
            status="stopped",
            reply="run cancelled",
            strategy=str(active_run.metadata.get("strategy") or "simple"),
        )
        await self._finalize_run(active_run)
        return StopRunResponse(run_id=run_id, status="stopping")

    async def get_active_run(self, run_id: str) -> Optional[ActiveRun]:
        async with self._lock:
            return self._active_runs.get(run_id)

    async def get_active_run_by_session(self, session_id: str) -> Optional[ActiveRun]:
        async with self._lock:
            run_id = self._busy_sessions.get(session_id)
            if not run_id:
                return None
            return self._active_runs.get(run_id)

    async def lookup_session_id(self, run_id: str) -> Optional[str]:
        async with self._lock:
            return self._known_run_sessions.get(run_id)

    async def shutdown(self) -> None:
        async with self._lock:
            active_runs = list(self._active_runs.values())
        for active_run in active_runs:
            await self.stop_run(active_run.run_id)
        for active_run in active_runs:
            if active_run.root_task is not None:
                await asyncio.gather(active_run.root_task, return_exceptions=True)

    async def _finalize_run(self, active_run: ActiveRun) -> None:
        await self.agent_roster_manager.teardown_run(active_run.run_id)
        async with self._lock:
            self._active_runs.pop(active_run.run_id, None)
            if self._busy_sessions.get(active_run.session_id) == active_run.run_id:
                self._busy_sessions.pop(active_run.session_id, None)

    async def _append_run_lifecycle_fact(
        self,
        active_run: ActiveRun,
        *,
        topic: str,
        status: str,
        reply: str,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> None:
        resolved_strategy = str(strategy or active_run.metadata.get("strategy") or "simple")
        payload = {
            "status": status,
            "reply": reply,
            "strategy": resolved_strategy,
            "session_id": active_run.session_id,
            "controller_agent_id": active_run.controller_agent_id,
            "user_agent_id": active_run.metadata.get("user_agent_id"),
            "assistant_agent_id": active_run.entry_assistant_id,
        }
        if error:
            payload["error"] = error
        await self.observation_center.append_shared_fact(
            session_id=active_run.session_id,
            run_id=active_run.run_id,
            message_id=message_id,
            sender_id="RunManager",
            target_agent_id=active_run.controller_agent_id,
            topic=topic,
            fact_type="run_lifecycle",
            payload_json=payload,
            metadata_json={"status": status, "strategy": resolved_strategy},
            visibility="public",
            level="error" if status == "error" else "info",
        )
