from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app_services import build_app_services
from main import create_app
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.message_center_repository import MessageCenterRepository
from repositories.session_repository import SessionRepository
from repositories.task_repository import TaskRepository


DEBUG_TRACE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".tauri-agent-next-data"
    / "debug"
    / "planner_coder_trace.json"
)


class AgentCollaborationTests(unittest.TestCase):
    def test_planner_and_coder_complete_scripted_collaboration_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
            with TestClient(app) as client:
                services = app.state.services
                session_repository = SessionRepository(services.sqlite_store)
                agent_instance_repository = AgentInstanceRepository(services.sqlite_store)
                task_repository = TaskRepository(services.sqlite_store)
                message_center_repository = MessageCenterRepository(services.sqlite_store)

                session_id = "session-planner-coder"
                asyncio.run(session_repository.create(session_id=session_id))
                user_proxy = asyncio.run(
                    services.agent_roster_manager.ensure_primary_user_proxy(session_id)
                )

                request_payload = {
                    "content": "给我写一个载具小程序",
                    "session_id": session_id,
                    "request_overrides": {
                        "tool_name": "send_event",
                        "tool_arguments": {
                            "topic": "task.plan",
                            "target_profile": "planner",
                            "payload": {
                                "content": "给我写一个载具小程序",
                                "session_id": session_id,
                                "planning_summary": "拆解需求，明确页面、数据和交互。",
                                "request_overrides": {
                                    "tool_name": "send_event",
                                    "tool_arguments": {
                                        "topic": "task.code",
                                        "target_profile": "coder",
                                        "payload": {
                                            "content": "根据规划生成载具小程序代码骨架",
                                            "session_id": session_id,
                                            "plan_summary": (
                                                "1. 首页展示载具列表；"
                                                "2. 详情页展示参数；"
                                                "3. 提供筛选与预约入口。"
                                            ),
                                            "request_overrides": {
                                                "tool_name": "send_event",
                                                "tool_arguments": {
                                                    "topic": "task.result",
                                                    "target_agent_id": user_proxy.agent_id,
                                                    "payload": {
                                                        "content": "Coder 已完成载具小程序代码骨架",
                                                        "session_id": session_id,
                                                        "code_summary": (
                                                            "包含车辆列表页、详情页、筛选表单和"
                                                            "预约按钮的微信小程序骨架。"
                                                        ),
                                                        "request_overrides": {
                                                            "tool_name": "finish_run",
                                                            "tool_arguments": {
                                                                "reply": (
                                                                    "Planner 已完成规划，"
                                                                    "Coder 已完成载具小程序代码骨架"
                                                                ),
                                                                "status": "completed",
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                }

                create_response = client.post("/runs", json=request_payload)
                self.assertEqual(create_response.status_code, 200)
                accepted = create_response.json()
                run_id = accepted["run_id"]
                self.assertEqual(accepted["session_id"], session_id)
                self.assertEqual(accepted["user_agent_id"], user_proxy.agent_id)

                snapshot_response = self._wait_for_snapshot(client, run_id)
                self.assertEqual(snapshot_response.status_code, 200)
                snapshot_payload = snapshot_response.json()
                self.assertEqual(snapshot_payload["snapshot"]["status"], "completed")
                self.assertEqual(snapshot_payload["run_projection"]["status"], "completed")
                self.assertIn(
                    "Planner 已完成规划，Coder 已完成载具小程序代码骨架",
                    str(snapshot_payload["run_projection"]["reply"] or ""),
                )

                agent_instances = asyncio.run(
                    agent_instance_repository.list_by_session(session_id)
                )
                tasks = self._wait_for_tasks(task_repository, run_id, require_finished=True)
                shared_messages = asyncio.run(
                    message_center_repository.list_latest_shared(session_id, limit=200)
                )
                events = client.get(
                    f"/runs/{run_id}/events",
                    params={"after_seq": 0, "limit": 200},
                ).json()["events"]

                profile_ids = [record.profile_id for record in agent_instances if record.profile_id]
                self.assertIn("planner", profile_ids)
                self.assertIn("coder", profile_ids)

                task_agent_ids = {task.agent_id for task in tasks}
                task_profiles = {
                    record.profile_id
                    for record in agent_instances
                    if record.id in task_agent_ids and record.profile_id
                }
                task_types = {
                    record.agent_type
                    for record in agent_instances
                    if record.id in task_agent_ids
                }
                self.assertIn("planner", task_profiles)
                self.assertIn("coder", task_profiles)
                self.assertIn("user_proxy", task_types)

                shared_topics = [record.topic for record in shared_messages]
                self.assertIn("task.plan", shared_topics)
                self.assertIn("task.code", shared_topics)
                self.assertIn("task.result", shared_topics)
                self.assertLess(shared_topics.index("task.plan"), shared_topics.index("task.code"))
                self.assertLess(shared_topics.index("task.code"), shared_topics.index("task.result"))

                self._write_debug_trace(
                    request_payload=request_payload,
                    accepted=accepted,
                    snapshot=snapshot_payload,
                    agent_instances=agent_instances,
                    tasks=tasks,
                    shared_messages=shared_messages,
                    events=events,
                )
                self.assertTrue(DEBUG_TRACE_PATH.exists())

                debug_payload = json.loads(DEBUG_TRACE_PATH.read_text(encoding="utf-8"))
                self.assertEqual(debug_payload["accepted"]["run_id"], run_id)
                self.assertEqual(debug_payload["accepted"]["session_id"], session_id)
                self.assertIn("planner", [item["profile_id"] for item in debug_payload["agent_instances"]])
                self.assertIn("coder", [item["profile_id"] for item in debug_payload["agent_instances"]])
                self.assertEqual(
                    [item["topic"] for item in debug_payload["shared_messages"] if item["topic"] in {"task.plan", "task.code", "task.result"}],
                    ["task.plan", "task.code", "task.result"],
                )

    def _wait_for_snapshot(self, client: TestClient, run_id: str):
        deadline = time.time() + 3.0
        last_response = None
        while time.time() < deadline:
            last_response = client.get(f"/runs/{run_id}/snapshot")
            if last_response.status_code == 200:
                status = last_response.json()["snapshot"]["status"]
                if status in {"completed", "error", "stopped"}:
                    return last_response
            time.sleep(0.02)
        return last_response

    def _wait_for_tasks(
        self,
        task_repository: TaskRepository,
        run_id: str,
        *,
        require_finished: bool,
    ):
        deadline = time.time() + 3.0
        tasks = []
        while time.time() < deadline:
            tasks = asyncio.run(task_repository.list_by_run(run_id))
            if tasks and (
                not require_finished
                or all(task.status in {"completed", "failed", "stopped"} for task in tasks)
            ):
                return tasks
            time.sleep(0.02)
        return tasks

    def _write_debug_trace(
        self,
        *,
        request_payload,
        accepted,
        snapshot,
        agent_instances,
        tasks,
        shared_messages,
        events,
    ) -> None:
        DEBUG_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request": request_payload,
            "accepted": accepted,
            "snapshot": snapshot,
            "agent_instances": [
                {
                    "id": record.id,
                    "session_id": record.session_id,
                    "agent_type": record.agent_type,
                    "profile_id": record.profile_id,
                    "role": record.role,
                    "display_name": record.display_name,
                    "metadata": dict(record.metadata),
                }
                for record in agent_instances
            ],
            "tasks": [
                {
                    "id": task.id,
                    "run_id": task.run_id,
                    "agent_id": task.agent_id,
                    "topic": task.topic,
                    "status": task.status,
                    "metadata": dict(task.metadata),
                    "result": task.result,
                    "error_text": task.error_text,
                }
                for task in tasks
            ],
            "shared_messages": [
                {
                    "message_id": record.message_id,
                    "message_type": record.message_type,
                    "rpc_phase": record.rpc_phase,
                    "topic": record.topic,
                    "sender_id": record.sender_id,
                    "target_agent_id": record.target_agent_id,
                    "target_profile": record.target_profile,
                    "payload": dict(record.payload),
                    "metadata": dict(record.metadata),
                    "seq": record.seq,
                }
                for record in shared_messages
            ],
            "events": events,
        }
        DEBUG_TRACE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
