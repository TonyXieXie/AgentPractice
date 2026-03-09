from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional
from uuid import uuid4

from agents.assistant import AssistantAgent
from agents.center import AgentCenter
from agents.instance import AgentInstance
from agents.user_proxy import UserProxyAgent
from models import CreateRunRequest, CreateRunResponse, StopRunResponse
from observation.center import ObservationCenter
from observation.events import ExecutionEvent


@dataclass
class ActiveRun:
    run_id: str
    user_agent_id: str
    assistant_agent_id: str
    user_agent: UserProxyAgent
    assistant_agent: AssistantAgent
    task: asyncio.Task[None]


class RunManager:
    def __init__(
        self,
        *,
        agent_center: AgentCenter,
        observation_center: ObservationCenter,
    ) -> None:
        self.agent_center = agent_center
        self.observation_center = observation_center
        self._active_runs: Dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        run_id = uuid4().hex
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

        await self.agent_center.register(user_agent)
        await self.agent_center.register(assistant_agent)

        task = asyncio.create_task(
            self._execute_run(user_agent, assistant_agent, request, run_id),
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
    ) -> None:
        try:
            response = await user_agent.send_user_message(
                request.content,
                target_agent_id=assistant_agent.agent_id,
                run_id=run_id,
                strategy=request.strategy,
                history=request.history,
                llm_config=request.llm_config,
                system_prompt=request.system_prompt,
                work_path=request.work_path,
                request_overrides=request.request_overrides,
            )
            if not response.ok:
                await self._ensure_terminal_event(
                    run_id,
                    status="error",
                    reply=str(response.payload.get("error") or ""),
                )
        except asyncio.CancelledError:
            await self._ensure_terminal_event(
                run_id,
                status="stopped",
                reply="run cancelled",
            )
            raise
        except Exception as exc:
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
