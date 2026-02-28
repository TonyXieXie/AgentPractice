from pathlib import Path

from database import Database
from models import ChatSessionCreate, LLMConfigCreate, TaskStatus


def _create_seed_session(test_db: Database) -> str:
    cfg = test_db.create_config(
        LLMConfigCreate(
            name="test",
            api_profile="openai",
            api_format="openai_chat_completions",
            api_key="k",
            model="gpt-4o-mini",
        )
    )
    session = test_db.create_session(ChatSessionCreate(title="s", config_id=cfg.id))
    return session.id


def test_migration_up_down_up_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "m1.sqlite"
    test_db = Database(str(db_path))
    test_db.migrate_agent_tasks_down()
    test_db.migrate_agent_tasks_up()
    test_db.migrate_agent_tasks_up()

    session_id = _create_seed_session(test_db)
    instance = test_db.upsert_agent_instance(session_id, "default", abilities=["a"])
    assert instance.session_id == session_id


def test_task_crud_and_event_seq(tmp_path: Path) -> None:
    db_path = tmp_path / "m1_crud.sqlite"
    test_db = Database(str(db_path))
    session_id = _create_seed_session(test_db)
    instance = test_db.upsert_agent_instance(session_id, "default", abilities=["tools_all"])

    task = test_db.create_agent_task(
        session_id=session_id,
        title="t1",
        input_text="hello",
        status=TaskStatus.pending,
        assigned_instance_id=instance.id,
        target_profile_id=instance.profile_id,
        required_abilities=["tools_all"],
        initial_event={"event_type": "task_progress", "status": TaskStatus.pending, "message": "created"},
    )
    assert task.status == TaskStatus.pending

    event2 = test_db.append_agent_task_event(
        task_id=task.id,
        event_type="task_started",
        status=TaskStatus.running,
        message="running",
    )
    event3 = test_db.append_agent_task_event(
        task_id=task.id,
        event_type="task_completed",
        status=TaskStatus.succeeded,
        message="done",
        payload={"result": "ok"},
    )
    assert event2.seq == 2
    assert event3.seq == 3

    updated = test_db.update_agent_task(task.id, status=TaskStatus.succeeded, result="ok")
    assert updated is not None
    assert updated.status == TaskStatus.succeeded
    assert updated.result == "ok"

    listed = test_db.list_agent_tasks(session_id=session_id)
    assert any(item.id == task.id for item in listed)


def test_transaction_create_task_with_initial_event(tmp_path: Path) -> None:
    db_path = tmp_path / "m1_tx.sqlite"
    test_db = Database(str(db_path))
    session_id = _create_seed_session(test_db)
    instance = test_db.upsert_agent_instance(session_id, "default", abilities=[])

    task = test_db.create_agent_task(
        session_id=session_id,
        title="tx",
        input_text="payload",
        status=TaskStatus.pending,
        assigned_instance_id=instance.id,
        initial_event={"event_type": "task_progress", "status": TaskStatus.pending, "message": "created"},
    )

    events = test_db.list_agent_task_events(task.id)
    assert len(events) == 1
    assert events[0].seq == 1
    assert events[0].event_type == "task_progress"
