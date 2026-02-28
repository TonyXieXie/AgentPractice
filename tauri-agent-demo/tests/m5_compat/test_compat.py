import asyncio
import json

from fastapi.testclient import TestClient

from models import TaskStatus
from tools.builtin.subagent_tool import SpawnSubagentTool


class _FakeTask:
    def __init__(self, task_id: str):
        self.id = task_id


class _FakeDoneTask:
    def __init__(self, task_id: str):
        self.id = task_id
        self.status = TaskStatus.succeeded
        self.result = "ok"
        self.error_message = None
        self.legacy_child_session_id = "child-1"


class _FakeOrchestrator:
    async def create_task(self, _request):
        return _FakeTask("task-1")

    async def wait_task_terminal(self, _task_id):
        return _FakeDoneTask("task-1")


def test_spawn_subagent_compat_fields_with_task_center(monkeypatch):
    tool = SpawnSubagentTool()

    monkeypatch.setattr('tools.builtin.subagent_tool._task_center_enabled', lambda: True)
    monkeypatch.setattr('tools.builtin.subagent_tool.get_tool_context', lambda: {'session_id': 'parent-1', 'task_id': 'parent-task'})
    monkeypatch.setattr(
        'tools.builtin.subagent_tool.prepare_subagent_session',
        lambda **kwargs: {
            'parent_session_id': 'parent-1',
            'child_session_id': 'child-1',
            'child_title': kwargs.get('title') or 'Subagent Task',
            'subagent_profile_id': kwargs.get('profile_id') or 'subagent',
            'processed_message': kwargs.get('task', ''),
            'user_message_id': 1,
            'assistant_message_id': None,
            'suppress_parent_notify': kwargs.get('suppress_parent_notify', False),
        },
    )

    async def _noop_notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr('tools.builtin.subagent_tool.notify_parent_subagent_started', _noop_notify)
    monkeypatch.setattr('tools.builtin.subagent_tool.get_task_orchestrator', lambda: _FakeOrchestrator())

    payload = json.dumps({'task': 'write tests', 'wait': False})
    result = asyncio.run(tool.execute(payload))
    parsed = json.loads(result)

    assert parsed['status'] == 'started'
    assert parsed['child_session_id'] == 'child-1'
    assert parsed['task_id'] == 'task-1'


def test_chat_agent_stream_resume_compat_path_exists():
    import main as backend_main

    with TestClient(backend_main.app) as client:
        response = client.post(
            '/chat/agent/stream',
            json={
                'message': 'resume',
                'resume': True,
                'stream_id': 'not-found',
                'last_seq': 0,
            },
        )
        assert response.status_code == 404
        text = response.text
        assert 'Stream not found' in text
