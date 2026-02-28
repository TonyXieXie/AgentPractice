from pydantic import ValidationError

from models import AgentTaskCreateRequest, TaskStatus
from task_orchestrator import is_valid_task_transition


def test_task_schema_accepts_valid_payload() -> None:
    payload = AgentTaskCreateRequest(
        session_id="s-1",
        input="implement feature",
        title="Feature task",
        target_profile_id="default",
        required_abilities=["tools_all"],
        metadata={"origin": "test"},
    )
    assert payload.session_id == "s-1"
    assert payload.input == "implement feature"
    assert payload.required_abilities == ["tools_all"]


def test_task_schema_rejects_missing_required_fields() -> None:
    try:
        AgentTaskCreateRequest(session_id="s-1")
    except ValidationError:
        pass
    else:
        raise AssertionError("Expected ValidationError for missing input")


def test_task_state_transition_matrix() -> None:
    assert is_valid_task_transition(TaskStatus.pending, TaskStatus.running)
    assert is_valid_task_transition(TaskStatus.running, TaskStatus.succeeded)
    assert is_valid_task_transition(TaskStatus.running, TaskStatus.failed)
    assert not is_valid_task_transition(TaskStatus.succeeded, TaskStatus.running)
    assert not is_valid_task_transition(TaskStatus.cancelled, TaskStatus.pending)
