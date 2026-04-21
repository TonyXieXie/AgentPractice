import os
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import patch

from database import Database
from models import BranchSessionCreateRequest, ChatMessageCreate, ChatSessionCreate
from repositories import chat_repository, session_repository

subagent_runner_stub = ModuleType('subagent_runner')
subagent_runner_stub.cancel_subagent_task = lambda *_args, **_kwargs: False
subagent_runner_stub.suppress_subagent_parent_notify = lambda *_args, **_kwargs: None
sys.modules.setdefault('subagent_runner', subagent_runner_stub)

pty_manager_stub = ModuleType('tools.pty_manager')


class _NoopPtyManager:
    def close_session(self, *_args, **_kwargs):
        return None


pty_manager_stub.get_pty_manager = lambda: _NoopPtyManager()
sys.modules.setdefault('tools.pty_manager', pty_manager_stub)

from server.services import session_service


class BranchSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.temp_dir.name, 'branch-tests.db'))
        self._patchers = [
            patch.object(session_repository, 'db', self.db),
            patch.object(chat_repository, 'db', self.db),
            patch.object(session_service, 'schedule_ast_scan', lambda *_args, **_kwargs: None),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def _count(self, query: str, params=()):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()

    def _count_child_tool_calls(self, session_id: str) -> int:
        return self._count(
            '''
            SELECT COUNT(*)
            FROM tool_calls
            WHERE message_id IN (
                SELECT id FROM chat_messages WHERE session_id = ?
            )
            ''',
            (session_id,),
        )

    def _seed_source_session(self):
        session = session_repository.create_session(
            ChatSessionCreate(
                title='Root Session',
                config_id='cfg-test',
                work_path='C:\\repo',
            )
        )
        session_repository.update_session_context(session.id, 'compressed summary', 321)
        session_repository.update_context_estimate(session.id, {'total': 123, 'system': 10, 'history': 80})

        user1 = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role='user',
                content='Question alpha',
            )
        )
        assistant1 = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role='assistant',
                content='Alpha beta gamma',
                metadata={},
            )
        )
        user2 = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role='user',
                content='Question delta',
            )
        )
        assistant2 = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role='assistant',
                content='Later branch should not copy this',
                metadata={},
            )
        )

        self.db.save_message_attachment(user1.id, 'before.txt', 'text/plain', b'before', size=6)
        self.db.save_message_attachment(assistant2.id, 'after.txt', 'text/plain', b'after', size=5)

        self.db.save_agent_step(assistant1.id, 'answer', 'Alpha beta gamma', 7, {'kind': 'final'})
        self.db.save_agent_step(assistant2.id, 'answer', 'Later branch should not copy this', 9, {'kind': 'final'})

        self.db.save_tool_call(assistant1.id, 'tool.before', '{}', '{"ok": true}')
        self.db.save_tool_call(assistant2.id, 'tool.after', '{}', '{"ok": true}')

        self.db.create_file_snapshot(session.id, assistant1.id, 'tree-before', 'C:\\repo')
        self.db.create_file_snapshot(session.id, assistant2.id, 'tree-after', 'C:\\repo')

        self.db.save_llm_call(
            session_id=session.id,
            message_id=assistant1.id,
            agent_type='react',
            iteration=1,
            stream=False,
            api_profile='openai',
            api_format='openai_chat_completions',
            model='gpt-test',
            request_json={'prompt': 'before'},
            response_json={'text': 'before'},
            response_text='before',
            processed_json={'answer': 'before'},
        )
        self.db.save_llm_call(
            session_id=session.id,
            message_id=assistant2.id,
            agent_type='react',
            iteration=2,
            stream=False,
            api_profile='openai',
            api_format='openai_chat_completions',
            model='gpt-test',
            request_json={'prompt': 'after'},
            response_json={'text': 'after'},
            response_text='after',
            processed_json={'answer': 'after'},
        )

        self.db.save_session_tool_call_history(
            session_id=session.id,
            tool_name='tool.before',
            success=True,
            message_id=assistant1.id,
            agent_type='react',
            iteration=1,
        )
        self.db.save_session_tool_call_history(
            session_id=session.id,
            tool_name='tool.after',
            success=True,
            message_id=assistant2.id,
            agent_type='react',
            iteration=2,
        )

        return {
            'session': session,
            'assistant1': assistant1,
            'assistant2': assistant2,
        }

    def _create_branch(self, session_id: str, source_message_id: int):
        return session_service.create_branch_session(
            session_id,
            BranchSessionCreateRequest(
                source_message_id=source_message_id,
                source_step_sequence=7,
                selection_start=0,
                selection_end=5,
                selected_text='Alpha',
            ),
        )

    def test_create_branch_session_copies_history_up_to_selected_message(self):
        seeded = self._seed_source_session()

        response = self._create_branch(seeded['session'].id, seeded['assistant1'].id)
        branch_session = response.branch_session

        self.assertEqual(branch_session.session_kind, 'branch')
        self.assertEqual(branch_session.parent_session_id, seeded['session'].id)
        self.assertIsNone(branch_session.context_summary)
        self.assertIsNone(branch_session.last_compressed_llm_call_id)
        self.assertIsNone(branch_session.context_estimate)
        self.assertIsNone(branch_session.context_estimate_at)

        branch_messages = session_repository.list_messages(branch_session.id)
        self.assertEqual([message.role for message in branch_messages], ['user', 'assistant'])
        self.assertEqual([message.content for message in branch_messages], ['Question alpha', 'Alpha beta gamma'])

        branch_steps = session_repository.list_agent_steps(branch_session.id)
        self.assertEqual(len(branch_steps), 1)
        self.assertEqual(branch_steps[0]['step_type'], 'answer')
        self.assertEqual(branch_steps[0]['sequence'], 7)

        self.assertEqual(self._count('SELECT COUNT(*) FROM message_attachments WHERE message_id IN (SELECT id FROM chat_messages WHERE session_id = ?)', (branch_session.id,)), 1)
        self.assertEqual(self._count_child_tool_calls(branch_session.id), 1)
        self.assertEqual(self._count('SELECT COUNT(*) FROM file_snapshots WHERE session_id = ?', (branch_session.id,)), 1)
        self.assertEqual(self._count('SELECT COUNT(*) FROM llm_calls WHERE session_id = ?', (branch_session.id,)), 1)
        self.assertEqual(self._count('SELECT COUNT(*) FROM session_tool_call_history WHERE session_id = ?', (branch_session.id,)), 1)

        source_message = session_repository.get_message_details(seeded['session'].id, seeded['assistant1'].id)
        branch_links = source_message['metadata'].get('branch_links') or []
        self.assertEqual(len(branch_links), 1)
        self.assertEqual(branch_links[0]['child_session_id'], branch_session.id)
        self.assertEqual(branch_links[0]['step_sequence'], 7)
        self.assertEqual(branch_links[0]['start_offset'], 0)
        self.assertEqual(branch_links[0]['end_offset'], 5)
        self.assertEqual(branch_links[0]['selected_text'], 'Alpha')
        self.assertFalse(response.existing)

    def test_duplicate_branch_request_reuses_existing_hidden_session(self):
        seeded = self._seed_source_session()

        first = self._create_branch(seeded['session'].id, seeded['assistant1'].id)
        second = self._create_branch(seeded['session'].id, seeded['assistant1'].id)

        self.assertEqual(first.branch_session.id, second.branch_session.id)
        self.assertTrue(second.existing)
        self.assertEqual(
            self._count(
                'SELECT COUNT(*) FROM chat_sessions WHERE parent_session_id = ? AND session_kind = ?',
                (seeded['session'].id, 'branch'),
            ),
            1,
        )

        source_message = session_repository.get_message_details(seeded['session'].id, seeded['assistant1'].id)
        branch_links = source_message['metadata'].get('branch_links') or []
        self.assertEqual(len(branch_links), 1)

    def test_delete_parent_session_also_deletes_branch_children(self):
        seeded = self._seed_source_session()
        response = self._create_branch(seeded['session'].id, seeded['assistant1'].id)

        deleted = session_repository.delete_session(seeded['session'].id)

        self.assertTrue(deleted)
        self.assertIsNone(session_repository.get_session(seeded['session'].id, include_count=False))
        self.assertIsNone(session_repository.get_session(response.branch_session.id, include_count=False))
        self.assertEqual(
            self._count('SELECT COUNT(*) FROM chat_sessions WHERE id IN (?, ?)', (seeded['session'].id, response.branch_session.id)),
            0,
        )


if __name__ == '__main__':
    unittest.main()
