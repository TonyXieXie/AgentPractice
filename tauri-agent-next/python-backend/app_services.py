from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.center import AgentCenter
from observation.center import ObservationCenter
from repositories.event_repository import FileEventStore
from run_manager import RunManager
from runtime_paths import ensure_runtime_dirs, get_runs_data_dir
from transport.ws.ws_hub import WsHub


@dataclass
class AppServices:
    ws_hub: WsHub
    event_store: FileEventStore
    observation_center: ObservationCenter
    agent_center: AgentCenter
    run_manager: RunManager
    data_dir: Path

    async def startup(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runs").mkdir(parents=True, exist_ok=True)
        self.ws_hub.set_loop(asyncio.get_running_loop())

    async def shutdown(self) -> None:
        await self.run_manager.shutdown()


def build_app_services(*, data_dir: Optional[Path] = None) -> AppServices:
    runtime_dir = Path(data_dir or ensure_runtime_dirs()).resolve()
    event_store = FileEventStore(get_runs_data_dir() if data_dir is None else runtime_dir / "runs")
    ws_hub = WsHub()
    observation_center = ObservationCenter(
        event_store=event_store,
        ws_hub=ws_hub,
    )
    agent_center = AgentCenter(observer=observation_center)
    run_manager = RunManager(
        agent_center=agent_center,
        observation_center=observation_center,
    )
    return AppServices(
        ws_hub=ws_hub,
        event_store=event_store,
        observation_center=observation_center,
        agent_center=agent_center,
        run_manager=run_manager,
        data_dir=runtime_dir,
    )
