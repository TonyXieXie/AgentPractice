from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.center import AgentCenter
from agents.roster_manager import AgentRosterManager
from agents.execution import TaskManager, ToolExecutor
from agents.execution.agent_memory import AgentMemory
from agents.execution.tool_recorder import ConversationToolRecorder
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from observation.center import ObservationCenter
from repositories.conversation_repository import ConversationRepository
from repositories.event_repository import FileEventStore
from repositories.message_center_repository import MessageCenterRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository
from run_manager import RunManager
from run_request_queue import RunRequestQueue
from runtime_paths import ensure_runtime_dirs, get_database_path, get_runs_data_dir
from transport.ws.ws_hub import WsHub


@dataclass
class AppServices:
    ws_hub: WsHub
    event_store: FileEventStore
    sqlite_store: SqliteStore
    observation_center: ObservationCenter
    agent_center: AgentCenter
    agent_profile_repository: AgentProfileRepository
    agent_roster_manager: AgentRosterManager
    task_repository: TaskRepository
    task_manager: TaskManager
    run_manager: RunManager
    run_request_queue: RunRequestQueue
    data_dir: Path

    async def startup(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runs").mkdir(parents=True, exist_ok=True)
        await self.sqlite_store.initialize()
        self.ws_hub.set_loop(asyncio.get_running_loop())

    async def shutdown(self) -> None:
        await self.run_manager.shutdown()
        await self.agent_roster_manager.shutdown()


def build_app_services(*, data_dir: Optional[Path] = None) -> AppServices:
    runtime_dir = Path(data_dir or ensure_runtime_dirs()).resolve()
    event_store = FileEventStore(get_runs_data_dir() if data_dir is None else runtime_dir / "runs")
    sqlite_store = SqliteStore(get_database_path(base_dir=runtime_dir))
    session_repository = SessionRepository(sqlite_store)
    agent_instance_repository = AgentInstanceRepository(sqlite_store)
    message_center_repository = MessageCenterRepository(sqlite_store)
    conversation_repository = ConversationRepository(sqlite_store)
    agent_prompt_state_repository = AgentPromptStateRepository(sqlite_store)
    prompt_trace_repository = PromptTraceRepository(sqlite_store)
    task_repository = TaskRepository(sqlite_store)
    memory = AgentMemory(
        session_repository=session_repository,
        message_center_repository=message_center_repository,
        conversation_repository=conversation_repository,
        agent_prompt_state_repository=agent_prompt_state_repository,
        prompt_trace_repository=prompt_trace_repository,
    )
    tool_executor = ToolExecutor(recorder=ConversationToolRecorder(conversation_repository))
    task_manager = TaskManager(task_repository)
    run_request_queue = RunRequestQueue()
    ws_hub = WsHub()
    observation_center = ObservationCenter(
        event_store=event_store,
        ws_hub=ws_hub,
    )
    agent_profile_repository = AgentProfileRepository()
    agent_center = AgentCenter(
        observer=observation_center,
        message_center_repository=message_center_repository,
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
    agent_roster_manager.bind_runtime_services(
        run_manager=run_manager,
        run_request_queue=run_request_queue,
    )
    return AppServices(
        ws_hub=ws_hub,
        event_store=event_store,
        sqlite_store=sqlite_store,
        observation_center=observation_center,
        agent_center=agent_center,
        agent_profile_repository=agent_profile_repository,
        agent_roster_manager=agent_roster_manager,
        task_repository=task_repository,
        task_manager=task_manager,
        run_manager=run_manager,
        run_request_queue=run_request_queue,
        data_dir=runtime_dir,
    )
