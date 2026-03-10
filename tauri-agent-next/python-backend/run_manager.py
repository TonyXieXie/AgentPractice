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
from repositories.conversation_repository import ConversationRepository
from repositories.session_repository import SessionRepository


@dataclass
class ActiveRun:
    run_id: str
    user_agent_id: str
    assistant_agent_id: str
    user_agent: UserProxyAgent
    assistant_agent: AssistantAgent
    task: asyncio.Task[None]


class SessionNotFoundError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"session not found: {session_id}")
        self.session_id = session_id


class RunManager:
    def __init__(
        self,
        *,
        agent_center: AgentCenter,
        observation_center: ObservationCenter,
        session_repository: SessionRepository,
        conversation_repository: ConversationRepository,
        prompt_manager,
        tool_executor: ToolExecutor,
    ) -> None:
        self.agent_center = agent_center
        self.observation_center = observation_center
        self.session_repository = session_repository
        self.conversation_repository = conversation_repository
        self.prompt_manager = prompt_manager
        self.tool_executor = tool_executor
        self._active_runs: Dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        run_id = uuid4().hex
        session_id, effective_llm_config, effective_system_prompt, effective_work_path = (
            await self._resolve_session_defaults(request)
        )
        user_agent_id = f"user-{run_id[:8]}"
        assistant_agent_id = f"assistant-{run_id[:8]}"
        user_agent = UserProxyAgent(
            AgentInstance(
                id=user_agent_id,
                agent_type="user_proxy",
                role="user_proxy",
                run_id=run_id,
            ),
            self.agent_center,
            observer=self.observation_center,
        )
        assistant_agent = AssistantAgent(
            AgentInstance(
                id=assistant_agent_id,
                agent_type="assistant",
                role="assistant",
                run_id=run_id,
            ),
            self.agent_center,
            observer=self.observation_center,
        )
        assistant_agent.execution_engine = ExecutionEngine(
            assistant_agent,
            context_builder=ContextBuilder(prompt_manager=self.prompt_manager),
            tool_executor=self.tool_executor,
        )

        await self.agent_center.register(user_agent)
        await self.agent_center.register(assistant_agent)

        await self.conversation_repository.append_event(
            session_id=session_id,
            run_id=run_id,
            kind="user_message",
            content={"text": request.content},
        )

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
            if not response.ok:
                await self._ensure_terminal_event(
                    run_id,
                    status="error",
                    reply=reply,
                )
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
            if reply:
                try:
                    await self.conversation_repository.append_event(
                        session_id=session_id,
                        run_id=run_id,
                        kind="assistant_message",
                        content={"text": reply},
                    )
                except Exception:
                    pass
            await self.agent_center.unregister(user_agent.agent_id)
            await self.agent_center.unregister(assistant_agent.agent_id)
            async with self._lock:
                self._active_runs.pop(run_id, None)

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
