from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.center import AgentCenter
from agents.execution import TaskManager, ToolExecutor
from agents.execution.agent_memory import AgentMemory
from agents.execution.tool_recorder import PrivateExecutionRecorder
from agents.roster_manager import AgentRosterManager
from app_logging import log_info
from observation.center import ObservationCenter
from observation.query_service import FactQueryService
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository
from run_manager import RunManager
from runtime_paths import ensure_runtime_dirs, get_database_path
from transport.ws.session_manager import WsSessionManager


@dataclass
class AppServices:
    sqlite_store: SqliteStore
    observation_center: ObservationCenter
    fact_query_service: FactQueryService
    ws_session_manager: WsSessionManager
    agent_center: AgentCenter
    agent_profile_repository: AgentProfileRepository
    agent_roster_manager: AgentRosterManager
    task_repository: TaskRepository
    task_manager: TaskManager
    run_manager: RunManager
    data_dir: Path

    async def startup(self) -> None:
        log_info("services.startup.begin", data_dir=str(self.data_dir))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        await self.sqlite_store.initialize()
        log_info("services.startup.complete", data_dir=str(self.data_dir))

    async def shutdown(self) -> None:
        log_info("services.shutdown.begin", data_dir=str(self.data_dir))
        await self.run_manager.shutdown()
        await self.agent_roster_manager.shutdown()
        log_info("services.shutdown.complete", data_dir=str(self.data_dir))


def build_app_services(*, data_dir: Optional[Path] = None) -> AppServices:
    runtime_dir = Path(data_dir or ensure_runtime_dirs()).resolve()
    sqlite_store = SqliteStore(get_database_path(base_dir=runtime_dir))
    session_repository = SessionRepository(sqlite_store)
    agent_instance_repository = AgentInstanceRepository(sqlite_store)
    shared_fact_repository = SharedFactRepository(sqlite_store)
    agent_private_event_repository = AgentPrivateEventRepository(sqlite_store)
    agent_prompt_state_repository = AgentPromptStateRepository(sqlite_store)
    prompt_trace_repository = PromptTraceRepository(sqlite_store)
    task_repository = TaskRepository(sqlite_store)

    fact_query_service = FactQueryService(
        shared_fact_repository=shared_fact_repository,
        agent_private_event_repository=agent_private_event_repository,
        prompt_trace_repository=prompt_trace_repository,
    )
    ws_session_manager = WsSessionManager(fact_query_service=fact_query_service)
    observation_center = ObservationCenter(
        shared_fact_repository=shared_fact_repository,
        agent_private_event_repository=agent_private_event_repository,
        fact_query_service=fact_query_service,
        ws_session_manager=ws_session_manager,
    )
    memory = AgentMemory(
        session_repository=session_repository,
        shared_fact_repository=shared_fact_repository,
        agent_private_event_repository=agent_private_event_repository,
        agent_prompt_state_repository=agent_prompt_state_repository,
        prompt_trace_repository=prompt_trace_repository,
        observation_center=observation_center,
    )
    tool_executor = ToolExecutor(
        recorder=PrivateExecutionRecorder(observation_center),
    )
    task_manager = TaskManager(task_repository)
    agent_profile_repository = AgentProfileRepository()
    agent_center = AgentCenter(
        observation_center=observation_center,
        session_repository=session_repository,
    )
    agent_roster_manager = AgentRosterManager(
        agent_center=agent_center,
        agent_instance_repository=agent_instance_repository,
        agent_profile_repository=agent_profile_repository,
        memory=memory,
        tool_executor=tool_executor,
        task_manager=task_manager,
    )
    agent_center.roster_manager = agent_roster_manager
    run_manager = RunManager(
        agent_center=agent_center,
        observation_center=observation_center,
        agent_roster_manager=agent_roster_manager,
        task_manager=task_manager,
    )
    agent_center.run_manager = run_manager
    agent_roster_manager.bind_runtime_services(run_manager=run_manager)
    log_info("services.build.complete", data_dir=str(runtime_dir))
    return AppServices(
        sqlite_store=sqlite_store,
        observation_center=observation_center,
        fact_query_service=fact_query_service,
        ws_session_manager=ws_session_manager,
        agent_center=agent_center,
        agent_profile_repository=agent_profile_repository,
        agent_roster_manager=agent_roster_manager,
        task_repository=task_repository,
        task_manager=task_manager,
        run_manager=run_manager,
        data_dir=runtime_dir,
    )
