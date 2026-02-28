import time
from pathlib import Path

import agent_instances as instance_mod
import task_orchestrator as orchestrator_mod
from database import Database
from fastapi.testclient import TestClient
from models import ChatSessionCreate, LLMConfigCreate, TaskStatus


class FastStubOrchestrator(orchestrator_mod.TaskOrchestrator):
    async def _execute_task(self, task):
        return {
            "result": f"ok:{task.input}",
            "child_session_id": f"child-{task.id[:8]}",
            "title": task.title,
        }


def _seed(test_db: Database) -> str:
    cfg = test_db.create_config(
        LLMConfigCreate(
            name="test",
            api_profile="openai",
            api_format="openai_chat_completions",
            api_key="k",
            model="gpt-4o-mini",
        )
    )
    session = test_db.create_session(ChatSessionCreate(title="api", config_id=cfg.id))
    return session.id


def _setup_backend(tmp_path: Path):
    import main as backend_main

    test_db = Database(str(tmp_path / "m3_api.sqlite"))
    session_id = _seed(test_db)

    orchestrator_mod.db = test_db
    instance_mod.db = test_db
    backend_main.db = test_db

    stub = FastStubOrchestrator(ws_hub=backend_main.WS_HUB)
    backend_main.TASK_ORCHESTRATOR = stub
    return backend_main, test_db, session_id


def _wait_terminal(client: TestClient, task_id: str, timeout_sec: float = 6.0):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        data = response.json()
        if data["status"] in ("succeeded", "failed", "cancelled"):
            return data
        time.sleep(0.1)
    raise AssertionError("Task did not reach terminal state")


def test_tasks_api_contract_and_after_seq(tmp_path: Path) -> None:
    backend_main, _db, session_id = _setup_backend(tmp_path)

    with TestClient(backend_main.app) as client:
        create_resp = client.post(
            "/tasks",
            json={
                "session_id": session_id,
                "title": "contract",
                "input": "hello",
            },
        )
        assert create_resp.status_code == 200
        task = create_resp.json()
        task_id = task["id"]

        final = _wait_terminal(client, task_id)
        assert final["status"] == TaskStatus.succeeded.value

        events_resp = client.get(f"/tasks/{task_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) >= 3
        seqs = [item["seq"] for item in events]
        assert seqs == sorted(seqs)

        after_resp = client.get(f"/tasks/{task_id}/events?after_seq={seqs[0]}")
        assert after_resp.status_code == 200
        after_events = after_resp.json()
        assert all(item["seq"] > seqs[0] for item in after_events)


def test_ws_task_seq_monotonic(tmp_path: Path) -> None:
    backend_main, _db, session_id = _setup_backend(tmp_path)

    with TestClient(backend_main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "subscribe", "session_ids": [session_id]})
            create_resp = client.post(
                "/tasks",
                json={
                    "session_id": session_id,
                    "title": "ws",
                    "input": "ping",
                },
            )
            assert create_resp.status_code == 200
            task_id = create_resp.json()["id"]

            seqs = []
            terminal = False
            deadline = time.time() + 6
            while time.time() < deadline and not terminal:
                payload = websocket.receive_json()
                if payload.get("task_id") != task_id:
                    continue
                if payload.get("type") not in {
                    "task_started",
                    "task_progress",
                    "task_handoff",
                    "task_completed",
                    "task_failed",
                    "task_cancelled",
                }:
                    continue
                seq = payload.get("seq")
                if isinstance(seq, int):
                    seqs.append(seq)
                if payload.get("type") in {"task_completed", "task_failed", "task_cancelled"}:
                    terminal = True

            assert terminal, "expected terminal websocket event"
            assert seqs == sorted(seqs)
            assert len(seqs) == len(set(seqs))
