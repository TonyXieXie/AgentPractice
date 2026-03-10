from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional
from uuid import uuid4

from agents.assistant import AssistantAgent
from agents.center import AgentCenter
from agents.execution import ContextBuilder, ExecutionEngine, ToolExecutor
from agents.instance import AgentInstance
from agents.user_proxy import UserProxyAgent
from models import CreateRunRequest, CreateRunResponse, StopRunResponse
from observation.center import ObservationCenter
from observation.events import ExecutionEvent
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.session_repository import SessionRepository


@dataclass
class ActiveRun:
    run_id: str
    session_id: str
    user_agent_id: str
    assistant_agent_id: str
    user_agent: UserProxyAgent
    assistant_agent: AssistantAgent
    task: asyncio.Task[None]


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
        session_repository: SessionRepository,
        agent_instance_repository: AgentInstanceRepository,
        memory,
        tool_executor: ToolExecutor,
    ) -> None:
        self.agent_center = agent_center
        self.observation_center = observation_center
        self.session_repository = session_repository
        self.agent_instance_repository = agent_instance_repository
        self.memory = memory
        self.tool_executor = tool_executor
        self._active_runs: Dict[str, ActiveRun] = {}
        self._busy_sessions: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        run_id = uuid4().hex
        session_id, effective_llm_config, effective_system_prompt, effective_work_path = (
            await self._resolve_session_defaults(request)
        )
        async with self._lock:
            active_run_id = self._busy_sessions.get(session_id)
            if active_run_id:
                raise SessionBusyError(session_id, active_run_id)
            self._busy_sessions[session_id] = run_id

        user_agent: Optional[UserProxyAgent] = None
        assistant_agent: Optional[AssistantAgent] = None
        try:
            user_record = await self.agent_instance_repository.get_or_create_primary(
                session_id,
                "user_proxy",
                display_name="UserProxy",
            )
            assistant_record = await self.agent_instance_repository.get_or_create_primary(
                session_id,
                "assistant",
                display_name="Assistant",
            )
            user_agent_id = user_record.id
            assistant_agent_id = assistant_record.id

            user_agent = UserProxyAgent(
                AgentInstance(
                    id=user_record.id,
                    agent_type=user_record.agent_type,
                    role=user_record.role,
                    run_id=run_id,
                    profile_id=user_record.profile_id,
                    metadata=dict(user_record.metadata),
                    created_at=user_record.created_at,
                    updated_at=user_record.updated_at,
                ),
                self.agent_center,
                observer=self.observation_center,
            )
            assistant_agent = AssistantAgent(
                AgentInstance(
                    id=assistant_record.id,
                    agent_type=assistant_record.agent_type,
                    role=assistant_record.role,
                    run_id=run_id,
                    profile_id=assistant_record.profile_id,
                    metadata=dict(assistant_record.metadata),
                    created_at=assistant_record.created_at,
                    updated_at=assistant_record.updated_at,
                ),
                self.agent_center,
                observer=self.observation_center,
            )
            assistant_agent.execution_engine = ExecutionEngine(
                assistant_agent,
                context_builder=ContextBuilder(memory=self.memory),
                tool_executor=self.tool_executor,
            )

            await self.agent_center.register(user_agent)
            await self.agent_center.register(assistant_agent)

            task = asyncio.create_task(
                self._execute_run(
                    user_agent,
                    assistant_agent,
                    request,
                    run_id,
                    session_id=session_id,
                    llm_config=effective_llm_config,
                    system_prompt=effective_system_prompt,
                    work_path=effective_work_path,
                ),
                name=f"run:{run_id}",
            )
            async with self._lock:
                self._active_runs[run_id] = ActiveRun(
                    run_id=run_id,
                    session_id=session_id,
                    user_agent_id=user_agent_id,
                    assistant_agent_id=assistant_agent_id,
                    user_agent=user_agent,
                    assistant_agent=assistant_agent,
                    task=task,
                )

            return CreateRunResponse(
                run_id=run_id,
                session_id=session_id,
                user_agent_id=user_agent_id,
                assistant_agent_id=assistant_agent_id,
                status="accepted",
            )
        except Exception:
            if user_agent is not None:
                await self.agent_center.unregister(user_agent.agent_id)
            if assistant_agent is not None:
                await self.agent_center.unregister(assistant_agent.agent_id)
            async with self._lock:
                if self._busy_sessions.get(session_id) == run_id:
                    self._busy_sessions.pop(session_id, None)
            raise

    async def stop_run(self, run_id: str) -> Optional[StopRunResponse]:
        active_run = await self.get_active_run(run_id)
        if active_run is None:
            snapshot = await self.observation_center.get_snapshot(run_id)
            if snapshot is None:
                return None
            return StopRunResponse(run_id=run_id, status=snapshot.status)
        if not active_run.task.done():
            active_run.task.cancel()
            return StopRunResponse(run_id=run_id, status="stopping")
        snapshot = await self.observation_center.get_snapshot(run_id)
        return StopRunResponse(run_id=run_id, status=snapshot.status if snapshot else "finished")

    async def get_active_run(self, run_id: str) -> Optional[ActiveRun]:
        async with self._lock:
            return self._active_runs.get(run_id)

    async def shutdown(self) -> None:
        async with self._lock:
            active_runs = list(self._active_runs.values())
        for active_run in active_runs:
            if not active_run.task.done():
                active_run.task.cancel()
        if active_runs:
            await asyncio.gather(
                *(active_run.task for active_run in active_runs),
                return_exceptions=True,
            )

    async def _execute_run(
        self,
        user_agent: UserProxyAgent,
        assistant_agent: AssistantAgent,
        request: CreateRunRequest,
        run_id: str,
        *,
        session_id: str,
        llm_config: Optional[Dict[str, object]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
    ) -> None:
        reply = ""
        try:
            response = await user_agent.send_user_message(
                request.content,
                target_agent_id=assistant_agent.agent_id,
                run_id=run_id,
                session_id=session_id,
                strategy=request.strategy,
                history=request.history,
                llm_config=llm_config if llm_config is not None else request.llm_config,
                system_prompt=system_prompt
                if system_prompt is not None
                else request.system_prompt,
                work_path=work_path if work_path is not None else request.work_path,
                request_overrides=request.request_overrides,
            )
            reply = str(response.payload.get("reply") or response.payload.get("error") or "")
        except asyncio.CancelledError:
            reply = "run cancelled"
            await self._ensure_terminal_event(
                run_id,
                status="stopped",
                reply=reply,
            )
            raise
        except Exception as exc:
            reply = str(exc)
            await self.observation_center.emit(
                ExecutionEvent(
                    event_type="run.error",
                    run_id=run_id,
                    agent_id=assistant_agent.agent_id,
                    visibility="internal",
                    level="error",
                    source_type="engine",
                    source_id=assistant_agent.agent_id,
                    tags=["run", "error"],
                    payload={
                        "status": "error",
                        "error": str(exc),
                        "message": str(exc),
                    },
                )
            )
        finally:
            await self.agent_center.unregister(user_agent.agent_id)
            await self.agent_center.unregister(assistant_agent.agent_id)
            async with self._lock:
                self._active_runs.pop(run_id, None)
                if self._busy_sessions.get(session_id) == run_id:
                    self._busy_sessions.pop(session_id, None)

    async def _ensure_terminal_event(
        self,
        run_id: str,
        *,
        status: str,
        reply: str,
    ) -> None:
        snapshot = await self.observation_center.get_snapshot(run_id)
        if snapshot and snapshot.latest_event_type == "run.finished":
            return
        active_run = await self.get_active_run(run_id)
        agent_id = active_run.assistant_agent_id if active_run else None
        await self.observation_center.emit(
            ExecutionEvent(
                event_type="run.finished",
                run_id=run_id,
                agent_id=agent_id,
                level="info" if status == "completed" else "error",
                source_type="engine",
                source_id=agent_id,
                tags=["run", status],
                payload={
                    "status": status,
                    "reply": reply,
                    "topic": "run.finished",
                },
            )
        )

    async def _resolve_session_defaults(
        self,
        request: CreateRunRequest,
    ) -> tuple[str, Optional[Dict[str, object]], Optional[str], Optional[str]]:
        if request.session_id:
            session = await self.session_repository.get(request.session_id)
            if session is None:
                raise SessionNotFoundError(request.session_id)
            if request.system_prompt is not None or request.work_path is not None or request.llm_config is not None:
                session = await self.session_repository.update_defaults(
                    request.session_id,
                    system_prompt=request.system_prompt,
                    work_path=request.work_path,
                    llm_config=request.llm_config,
                )
            effective_llm_config = (
                request.llm_config if request.llm_config is not None else (session.llm_config if session else None)
            )
            effective_system_prompt = (
                request.system_prompt if request.system_prompt is not None else (session.system_prompt if session else None)
            )
            effective_work_path = (
                request.work_path if request.work_path is not None else (session.work_path if session else None)
            )
            return request.session_id, effective_llm_config, effective_system_prompt, effective_work_path

        session_id = uuid4().hex
        created = await self.session_repository.create(
            session_id=session_id,
            system_prompt=request.system_prompt,
            work_path=request.work_path,
            llm_config=request.llm_config,
        )
        return session_id, created.llm_config, created.system_prompt, created.work_path
