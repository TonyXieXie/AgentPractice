from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app_config import set_app_config_path
from agents.roster_manager import AgentRosterManager
from agents.execution import TaskManager, ToolExecutor
from agents.message import utc_now_iso
from observation.center import ObservationCenter
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.agent_profile_repository import AgentProfileRepository
from repositories.event_repository import FileEventStore
from repositories.session_repository import SessionRepository
from repositories.sqlite_store import SqliteStore
from repositories.task_repository import TaskRepository
from run_manager import RunManager
from transport.ws.ws_hub import WsHub


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        runtime_dir = Path(self._temp_dir.name)
        config_path = runtime_dir / "app_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agent": {
                        "default_profile": "default",
                        "profiles": {
                            "default": {
                                "agent_type": "assistant",
                                "display_name": "Assistant",
                                "subscribed_topics": ["workflow.updated"],
                                "executable_event_topics": [],
                            },
                            "worker": {
                                "agent_type": "assistant",
                                "display_name": "Worker",
                                "subscribed_topics": ["workflow.updated"],
                                "executable_event_topics": ["worker.execute"],
                            },
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        set_app_config_path(config_path)
        self.store = SqliteStore(runtime_dir / "agent_next.db")
        await self.store.initialize()
        self.session_repository = SessionRepository(self.store)
        self.agent_instance_repository = AgentInstanceRepository(self.store)
        self.agent_profile_repository = AgentProfileRepository()
        self.task_repository = TaskRepository(self.store)
        self.task_manager = TaskManager(self.task_repository)
        self.event_store = FileEventStore(runtime_dir / "runs")
        self.ws_hub = WsHub()
        self.observation_center = ObservationCenter(
            event_store=self.event_store,
            ws_hub=self.ws_hub,
        )
        from agents.center import AgentCenter

        self.agent_center = AgentCenter(observer=self.observation_center)
        self.agent_roster_manager = AgentRosterManager(
            agent_center=self.agent_center,
            agent_instance_repository=self.agent_instance_repository,
            agent_profile_repository=self.agent_profile_repository,
            memory=None,
            tool_executor=ToolExecutor(),
            task_manager=self.task_manager,
        )
        self.agent_center.roster_manager = self.agent_roster_manager
        self.run_manager = RunManager(
            agent_center=self.agent_center,
            observation_center=self.observation_center,
            agent_roster_manager=self.agent_roster_manager,
            task_manager=self.task_manager,
        )
        self.agent_center.run_manager = self.run_manager
        self.agent_roster_manager.bind_runtime_services(
            run_manager=self.run_manager,
        )
        self.session_id = "session-1"
        await self.session_repository.create(session_id=self.session_id)
        self.primary_assistant = await self.agent_roster_manager.ensure_primary_entry_assistant(
            self.session_id
        )
        await self._insert_assistant_record("assistant-2")

    async def asyncTearDown(self) -> None:
        await self.run_manager.shutdown()
        await self.agent_roster_manager.shutdown()
        await self.agent_center.drain()
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_open_run_registers_session_assistants_and_finish_unregisters_after_finish(self) -> None:
        active_run = await self.run_manager.open_run(
            self.session_id,
            self.primary_assistant.id,
            metadata={"strategy": "simple"},
        )

        self.assertEqual(set(active_run.runtime_agents.keys()), {
            self.primary_assistant.id,
            "assistant-2",
        })
        registered = await self.agent_center.list_agents()
        self.assertEqual({agent.agent_id for agent in registered}, set(active_run.runtime_agents.keys()))

        await self.run_manager.finish_run(
            active_run.run_id,
            {"reply": "done", "status": "completed", "strategy": "simple"},
        )

        events = await self.observation_center.list_events(active_run.run_id, limit=50)
        self.assertEqual([event.event_type for event in events].count("run.started"), 1)
        self.assertEqual([event.event_type for event in events].count("run.finished"), 1)
        registered_after = await self.agent_center.list_agents()
        self.assertEqual(registered_after, [])

    async def test_stop_run_unregisters_runtime_roster(self) -> None:
        active_run = await self.run_manager.open_run(
            self.session_id,
            self.primary_assistant.id,
            metadata={"strategy": "simple"},
        )
        blocker = asyncio.Event()
        root_task = asyncio.create_task(blocker.wait())
        await self.run_manager.attach_root_task(active_run.run_id, root_task)

        stop_response = await self.run_manager.stop_run(active_run.run_id)
        self.assertIsNotNone(stop_response)
        assert stop_response is not None
        self.assertEqual(stop_response.status, "stopping")

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(root_task, timeout=1.0)

        snapshot = await self.observation_center.get_snapshot(active_run.run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.status, "stopped")
        registered_after = await self.agent_center.list_agents()
        self.assertEqual(registered_after, [])

    async def test_finish_run_unregisters_dynamic_profile_assistant(self) -> None:
        active_run = await self.run_manager.open_run(
            self.session_id,
            self.primary_assistant.id,
            metadata={"strategy": "simple"},
        )

        dynamic_agent = await self.agent_roster_manager.ensure_profile_instance(
            self.session_id,
            active_run.run_id,
            "worker",
        )
        self.assertIn(dynamic_agent.agent_id, active_run.runtime_agents)
        registered = await self.agent_center.list_agents()
        self.assertIn(dynamic_agent.agent_id, {agent.agent_id for agent in registered})

        await self.run_manager.finish_run(
            active_run.run_id,
            {"reply": "done", "status": "completed", "strategy": "simple"},
        )

        registered_after = await self.agent_center.list_agents()
        self.assertEqual(registered_after, [])

    async def _insert_assistant_record(self, agent_id: str) -> None:
        now = utc_now_iso()
        await self.store.execute(
            """
            INSERT INTO agent_instances
              (id, session_id, agent_type, profile_id, role, display_name, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                agent_id,
                self.session_id,
                "assistant",
                "default",
                "assistant",
                "Assistant 2",
                json.dumps({}, ensure_ascii=False),
                now,
                now,
            ),
            commit=True,
        )
