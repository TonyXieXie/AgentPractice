from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_services import build_app_services
from llm.default_config import clear_default_llm_config_cache
from main import create_app
from repositories.agent_instance_repository import AgentInstanceRepository
from repositories.shared_fact_repository import SharedFactRepository
from repositories.task_repository import TaskRepository


class AgentCollaborationTests(unittest.TestCase):
    def test_planner_and_coder_complete_scripted_collaboration_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            disabled_demo_db = Path(temp_dir) / "missing-demo-chat.db"
            with patch.dict(
                os.environ,
                {"TAURI_AGENT_NEXT_DEMO_DB_PATH": str(disabled_demo_db)},
                clear=False,
            ):
                clear_default_llm_config_cache()
                app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
                with TestClient(app) as client:
                    services = app.state.services
                    agent_instance_repository = AgentInstanceRepository(services.sqlite_store)
                    task_repository = TaskRepository(services.sqlite_store)
                    shared_fact_repository = SharedFactRepository(services.sqlite_store)

                    request_payload = {
                        "content": "给我写一个载具小程序",
                        "request_overrides": {
                            "tool_name": "handoff",
                            "tool_arguments": {
                                "target_profile": "planner",
                                "instruction": "给我写一个载具小程序",
                                "reason": "需要先拆解需求，明确页面、数据和交互。",
                                "context": {
                                    "planning_summary": "拆解需求，明确页面、数据和交互。",
                                    "request_overrides": {
                                        "tool_name": "handoff",
                                        "tool_arguments": {
                                            "target_profile": "coder",
                                            "instruction": "根据规划生成载具小程序代码骨架",
                                            "reason": "规划完成，交给 Coder 生成代码骨架。",
                                            "context": {
                                                "plan_summary": "列表、详情、筛选、预约。",
                                                "request_overrides": {
                                                    "tool_name": "finish_run",
                                                    "tool_arguments": {
                                                        "reply": "Planner 已完成规划，Coder 已完成载具小程序代码骨架",
                                                        "status": "completed",
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
                    session_id = accepted["session_id"]

                    shared_payload = self._wait_for_run_finished(client, session_id)
                    facts = shared_payload["shared_facts"]
                    topics = [fact["topic"] for fact in facts]
                    self.assertIn("task.plan", topics)
                    self.assertIn("task.code", topics)
                    self.assertIn("run.finished", topics)
                    self.assertNotIn("agent.execution_error", topics)
                    self.assertLess(topics.index("task.plan"), topics.index("task.code"))
                    finished_fact = next(
                        fact for fact in reversed(facts) if fact["topic"] == "run.finished"
                    )
                    self.assertEqual(finished_fact["payload_json"]["status"], "completed")

                    import asyncio

                    agent_instances = asyncio.run(
                        agent_instance_repository.list_by_session(session_id)
                    )
                    tasks = asyncio.run(task_repository.list_by_run(accepted["run_id"]))
                    shared_records = asyncio.run(
                        shared_fact_repository.list(session_id, after_seq=0, limit=200)
                    )

                    profile_ids = [record.profile_id for record in agent_instances if record.profile_id]
                    self.assertIn("planner", profile_ids)
                    self.assertIn("coder", profile_ids)
                    self.assertTrue(tasks)
                    self.assertEqual(
                        [record.topic for record in shared_records if record.topic in {"task.plan", "task.code"}],
                        ["task.plan", "task.code"],
                    )
                    plan_fact = next(record for record in shared_records if record.topic == "task.plan")
                    code_fact = next(record for record in shared_records if record.topic == "task.code")
                    self.assertEqual(plan_fact.payload["handoff_to_profile"], "planner")
                    self.assertEqual(code_fact.payload["handoff_to_profile"], "coder")
                    self.assertEqual(code_fact.payload["handoff_from_profile"], "planner")
                    self.assertEqual(code_fact.payload["request_overrides"]["tool_name"], "send_event")
                    self.assertEqual(
                        code_fact.payload["request_overrides"]["tool_arguments"]["topic"],
                        "run.controller.input",
                    )
                clear_default_llm_config_cache()

    def test_invalid_handoff_fails_run_and_marks_task_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            disabled_demo_db = Path(temp_dir) / "missing-demo-chat.db"
            with patch.dict(
                os.environ,
                {"TAURI_AGENT_NEXT_DEMO_DB_PATH": str(disabled_demo_db)},
                clear=False,
            ):
                clear_default_llm_config_cache()
                app = create_app(services=build_app_services(data_dir=Path(temp_dir)))
                with TestClient(app) as client:
                    services = app.state.services
                    task_repository = TaskRepository(services.sqlite_store)

                    response = client.post(
                        "/runs",
                        json={
                            "content": "把任务交给不存在的 profile",
                            "request_overrides": {
                                "tool_name": "handoff",
                                "tool_arguments": {
                                    "target_profile": "missing",
                                    "instruction": "请接手",
                                },
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    accepted = response.json()
                    session_id = accepted["session_id"]

                    shared_payload = self._wait_for_run_finished(client, session_id)
                    facts = shared_payload["shared_facts"]
                    finished_fact = next(
                        fact for fact in reversed(facts) if fact["topic"] == "run.finished"
                    )
                    self.assertEqual(finished_fact["payload_json"]["status"], "error")
                    self.assertIn(
                        "handoff target profile not found",
                        finished_fact["payload_json"]["error"],
                    )

                    import asyncio

                    tasks = asyncio.run(task_repository.list_by_run(accepted["run_id"]))
                    self.assertTrue(tasks)
                    self.assertEqual(tasks[-1].status, "failed")
                clear_default_llm_config_cache()

    def _wait_for_run_finished(self, client: TestClient, session_id: str):
        deadline = time.time() + 4.0
        last_payload = None
        while time.time() < deadline:
            response = client.get(f"/sessions/{session_id}/facts/shared")
            if response.status_code == 200:
                last_payload = response.json()
                if any(item["topic"] == "run.finished" for item in last_payload["shared_facts"]):
                    return last_payload
            time.sleep(0.05)
        return last_payload
