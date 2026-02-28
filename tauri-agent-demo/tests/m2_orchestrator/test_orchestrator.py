import asyncio
from pathlib import Path

import agent_instances as instance_mod
import task_orchestrator as orchestrator_mod
from database import Database
from models import AgentTaskCancelRequest, AgentTaskCreateRequest, AgentTaskHandoffRequest, ChatSessionCreate, LLMConfigCreate, TaskStatus


class DummyHub:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, session_id: str, payload):
        self.events.append((session_id, payload))


class StubOrchestrator(orchestrator_mod.TaskOrchestrator):
    def __init__(self, ws_hub: DummyHub):
        super().__init__(ws_hub=ws_hub)
        self.counter = 0

    async def _execute_task(self, task):
        self.counter += 1
        return {
            "result": f"done-{self.counter}",
            "child_session_id": f"child-{self.counter}",
            "title": task.title,
        }


class RetryOrchestrator(orchestrator_mod.TaskOrchestrator):
    def __init__(self, ws_hub: DummyHub):
        super().__init__(ws_hub=ws_hub)
        self.failed_once = False

    async def _execute_task(self, task):
        if not self.failed_once:
            self.failed_once = True
            raise TimeoutError("transient timeout")
        return {"result": "recovered", "child_session_id": "child-retry", "title": task.title}


def _seed(db: Database) -> str:
    cfg = db.create_config(
        LLMConfigCreate(
            name="test",
            api_profile="openai",
            api_format="openai_chat_completions",
            api_key="k",
            model="gpt-4o-mini",
        )
    )
    session = db.create_session(ChatSessionCreate(title="s", config_id=cfg.id))
    return session.id


def test_orchestrator_chain_and_handoff(tmp_path: Path) -> None:
    test_db = Database(str(tmp_path / "m2_chain.sqlite"))
    session_id = _seed(test_db)

    orchestrator_mod.db = test_db
    instance_mod.db = test_db

    hub = DummyHub()
    orch = StubOrchestrator(ws_hub=hub)

    async def scenario():
        await orch.start()
        root = await orch.create_task(AgentTaskCreateRequest(session_id=session_id, input="A"))
        root_done = await orch.wait_task_terminal(root.id, timeout_sec=8)
        assert root_done.status == TaskStatus.succeeded

        child = await orch.handoff_task(root.id, AgentTaskHandoffRequest(input="B"))
        child_done = await orch.wait_task_terminal(child.id, timeout_sec=8)
        assert child_done.status == TaskStatus.succeeded
        assert child_done.parent_task_id == root.id
        await orch.stop()

    asyncio.run(scenario())


def test_orchestrator_cancel_propagation(tmp_path: Path) -> None:
    test_db = Database(str(tmp_path / "m2_cancel.sqlite"))
    session_id = _seed(test_db)
    instance = test_db.upsert_agent_instance(session_id, "default", abilities=[])

    parent = test_db.create_agent_task(
        session_id=session_id,
        title="parent",
        input_text="P",
        status=TaskStatus.pending,
        assigned_instance_id=instance.id,
        target_profile_id=instance.profile_id,
    )
    child = test_db.create_agent_task(
        session_id=session_id,
        title="child",
        input_text="C",
        status=TaskStatus.pending,
        assigned_instance_id=instance.id,
        target_profile_id=instance.profile_id,
        parent_task_id=parent.id,
        root_task_id=parent.id,
    )
    test_db.add_agent_task_edge(parent.id, child.id, "handoff")

    orchestrator_mod.db = test_db
    instance_mod.db = test_db

    hub = DummyHub()
    orch = StubOrchestrator(ws_hub=hub)

    async def scenario():
        await orch.start()
        cancelled = await orch.cancel_task(parent.id, AgentTaskCancelRequest(reason="cancel", propagate=True))
        assert cancelled.status == TaskStatus.cancelled
        child_task = test_db.get_agent_task(child.id)
        assert child_task is not None
        assert child_task.status == TaskStatus.cancelled
        await orch.stop()

    asyncio.run(scenario())


def test_orchestrator_retry_transient_error(tmp_path: Path) -> None:
    test_db = Database(str(tmp_path / "m2_retry.sqlite"))
    session_id = _seed(test_db)

    orchestrator_mod.db = test_db
    instance_mod.db = test_db

    hub = DummyHub()
    orch = RetryOrchestrator(ws_hub=hub)

    async def scenario():
        await orch.start()
        task = await orch.create_task(AgentTaskCreateRequest(session_id=session_id, input="retry", max_retries=2))
        done = await orch.wait_task_terminal(task.id, timeout_sec=8)
        assert done.status == TaskStatus.succeeded
        assert done.retry_count == 1
        await orch.stop()

    asyncio.run(scenario())
