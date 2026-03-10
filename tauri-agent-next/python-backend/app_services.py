from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.center import AgentCenter
from agents.execution import ToolExecutor
from agents.execution.agent_memory import AgentMemory
from agents.execution.tool_recorder import ConversationToolRecorder
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from observation.center import ObservationCenter
from repositories.conversation_repository import ConversationRepository
from repositories.event_repository import FileEventStore
from repositories.message_center_repository import MessageCenterRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from run_manager import RunManager
from runtime_paths import ensure_runtime_dirs, get_database_path, get_runs_data_dir
from transport.ws.ws_hub import WsHub


@dataclass
class AppServices:
    ws_hub: WsHub
    event_store: FileEventStore
    sqlite_store: SqliteStore
    observation_center: ObservationCenter
    agent_center: AgentCenter
    run_manager: RunManager
    data_dir: Path

    async def startup(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runs").mkdir(parents=True, exist_ok=True)
        await self.sqlite_store.initialize()
        self.ws_hub.set_loop(asyncio.get_running_loop())

    async def shutdown(self) -> None:
        await self.run_manager.shutdown()


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
    memory = AgentMemory(
        session_repository=session_repository,
        message_center_repository=message_center_repository,
        conversation_repository=conversation_repository,
        agent_prompt_state_repository=agent_prompt_state_repository,
        prompt_trace_repository=prompt_trace_repository,
    )
    tool_executor = ToolExecutor(recorder=ConversationToolRecorder(conversation_repository))
    ws_hub = WsHub()
    observation_center = ObservationCenter(
        event_store=event_store,
        ws_hub=ws_hub,
    )
    agent_center = AgentCenter(
        observer=observation_center,
        message_center_repository=message_center_repository,
    )
    run_manager = RunManager(
        agent_center=agent_center,
        observation_center=observation_center,
        session_repository=session_repository,
        agent_instance_repository=agent_instance_repository,
        memory=memory,
        tool_executor=tool_executor,
    )
    return AppServices(
        ws_hub=ws_hub,
        event_store=event_store,
        sqlite_store=sqlite_store,
        observation_center=observation_center,
        agent_center=agent_center,
        run_manager=run_manager,
        data_dir=runtime_dir,
    )
