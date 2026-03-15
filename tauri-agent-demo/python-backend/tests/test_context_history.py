import os
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import context_compress
import database
from models import ChatMessageCreate, ChatSessionCreate, LLMConfigCreate
from repositories import chat_repository, config_repository, session_repository


class ContextHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_db = database.Database(os.path.join(self.tempdir.name, "context-history.sqlite3"))
        self._patched_attrs = []
        for module in (database, chat_repository, config_repository, session_repository):
            self._patch_attr(module, "db", self.temp_db)

        self.config = config_repository.create_config(
            LLMConfigCreate(
                name="test-config",
                api_profile="openai",
                api_key="test-key",
                model="gpt-test",
            )
        )

    def tearDown(self):
        while self._patched_attrs:
            module, attr_name, original_value = self._patched_attrs.pop()
            setattr(module, attr_name, original_value)
        self.tempdir.cleanup()

    def _patch_attr(self, module, attr_name, value):
        self._patched_attrs.append((module, attr_name, getattr(module, attr_name)))
        setattr(module, attr_name, value)

    def test_replays_agent_steps_from_empty_assistant_containers(self):
        session = session_repository.create_session(
            ChatSessionCreate(
                title="Coder Session",
                config_id=self.config.id,
                agent_profile="coder",
            )
        )
        initial_user = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="user", content="写一个载具模拟的框架")
        )
        container_message = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="assistant", content="")
        )
        chat_repository.save_agent_step(
            message_id=container_message.id,
            step_type="action",
            content="",
            sequence=0,
            metadata={"tool": "read_file", "input": {"path": "vehicle_sim/simulator.py"}},
            agent_profile="coder",
        )
        chat_repository.save_agent_step(
            message_id=container_message.id,
            step_type="observation",
            content="1: from __future__ import annotations",
            sequence=1,
            metadata={"tool": "read_file"},
            agent_profile="coder",
        )
        summary_message = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="[Coder]: 已检查 `vehicle_sim/simulator.py:1`，准备继续修改。",
            )
        )
        follow_up_user = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="user", content="继续修复 stop condition")
        )

        history = context_compress.build_history_for_llm(
            session_id=session.id,
            after_message_id=None,
            current_user_message_id=follow_up_user.id,
            summary="",
            code_map=None,
            current_agent_profile="coder",
        )

        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], initial_user.content)
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(
            history[1]["tool_calls"][0]["function"]["name"],
            "read_file",
        )
        self.assertEqual(history[2]["role"], "tool")
        self.assertIn("from __future__ import annotations", history[2]["content"])
        self.assertEqual(history[3]["role"], "assistant")
        self.assertEqual(history[3]["content"], summary_message.content)
        self.assertFalse(
            any(
                msg.get("role") == "assistant"
                and msg.get("content") == ""
                and not msg.get("tool_calls")
                for msg in history
            )
        )

    def test_keeps_handoff_before_replayed_steps_when_handoff_timestamp_is_updated(self):
        session = session_repository.create_session(
            ChatSessionCreate(
                title="Coder Session",
                config_id=self.config.id,
                agent_profile="coder",
            )
        )
        chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="user", content="写一个载具模拟的框架")
        )
        handoff_message = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="[Planner]: 先实现完整框架，然后再继续后续修改。",
            )
        )
        container_message = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="assistant", content="")
        )
        chat_repository.save_agent_step(
            message_id=container_message.id,
            step_type="action",
            content="",
            sequence=0,
            metadata={"tool": "read_file", "input": {"path": "vehicle_sim/vehicles/aircraft.py"}},
            agent_profile="coder",
        )
        chat_repository.save_agent_step(
            message_id=container_message.id,
            step_type="observation",
            content="1: class Helicopter:",
            sequence=1,
            metadata={"tool": "read_file"},
            agent_profile="coder",
        )
        chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="[Coder]: 已完成完整框架实现。",
            )
        )
        follow_up_user = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="user", content="让coder删除一下直升机代码")
        )

        conn = self.temp_db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE chat_messages SET timestamp = ? WHERE id = ?",
            ("9999-12-31T23:59:59", handoff_message.id),
        )
        conn.commit()
        conn.close()

        history = context_compress.build_history_for_llm(
            session_id=session.id,
            after_message_id=None,
            current_user_message_id=follow_up_user.id,
            summary="",
            code_map=None,
            current_agent_profile="coder",
        )

        planner_index = next(
            index
            for index, msg in enumerate(history)
            if msg.get("role") == "assistant" and "[Planner]:" in str(msg.get("content") or "")
        )
        tool_call_index = next(
            index
            for index, msg in enumerate(history)
            if msg.get("role") == "assistant"
            and msg.get("tool_calls")
            and msg["tool_calls"][0]["function"]["name"] == "read_file"
        )
        coder_report_index = next(
            index
            for index, msg in enumerate(history)
            if msg.get("role") == "assistant" and "[Coder]:" in str(msg.get("content") or "")
        )

        self.assertLess(planner_index, tool_call_index)
        self.assertLess(tool_call_index, coder_report_index)

    def test_list_messages_keeps_creation_order_when_timestamp_drifts(self):
        session = session_repository.create_session(
            ChatSessionCreate(
                title="Ordered Session",
                config_id=self.config.id,
                agent_profile="planner",
            )
        )
        first = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="user", content="first")
        )
        second = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="assistant", content="[Planner]: second")
        )
        third = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="assistant", content="[Coder]: third")
        )

        conn = self.temp_db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE chat_messages SET timestamp = ? WHERE id = ?",
            ("9999-12-31T23:59:59", second.id),
        )
        conn.commit()
        conn.close()

        all_messages = chat_repository.list_messages(session.id)
        latest_two = chat_repository.list_messages(session.id, limit=2)
        before_third = chat_repository.list_messages_before(session.id, third.id, 10)

        self.assertEqual([msg.id for msg in all_messages], [first.id, second.id, third.id])
        self.assertEqual([msg.id for msg in latest_two], [second.id, third.id])
        self.assertEqual([msg.id for msg in before_third], [first.id, second.id])

    def test_updating_message_content_does_not_mutate_creation_timestamp(self):
        session = session_repository.create_session(
            ChatSessionCreate(
                title="Timestamp Session",
                config_id=self.config.id,
                agent_profile="planner",
            )
        )
        message = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="initial",
                metadata={"event_type": "handoff", "handoff_id": "handoff-1"},
            )
        )
        original_timestamp = message.timestamp

        chat_repository.update_message_content(session.id, message.id, "updated once")
        details_after_content = chat_repository.get_message_details(session.id, message.id)

        chat_repository.update_message_content_and_metadata(
            session.id,
            message.id,
            "updated twice",
            {"event_type": "handoff", "handoff_id": "handoff-1", "event_kind": "completed"},
        )
        details_after_metadata = chat_repository.get_message_details(session.id, message.id)

        self.assertEqual(details_after_content["timestamp"], original_timestamp)
        self.assertEqual(details_after_metadata["timestamp"], original_timestamp)
        self.assertEqual(details_after_metadata["content"], "updated twice")
        self.assertEqual(details_after_metadata["metadata"]["event_kind"], "completed")


if __name__ == "__main__":
    unittest.main()
