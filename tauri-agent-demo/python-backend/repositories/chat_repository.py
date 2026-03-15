import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import db
from models import ChatMessage, ChatMessageCreate


def create_message(message: ChatMessageCreate) -> ChatMessage:
    return db.create_message(message)


def list_messages(session_id: str, limit: Optional[int] = None) -> List[ChatMessage]:
    return db.get_session_messages(session_id, limit)


def list_messages_before(session_id: str, before_id: int, limit: int) -> List[ChatMessage]:
    return db.get_session_messages_before(session_id, before_id, limit)


def get_message(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return db.get_message(session_id, message_id)


def get_message_details(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT id, session_id, role, content, timestamp, metadata
        FROM chat_messages
        WHERE id = ? AND session_id = ?
        ''',
        (message_id, session_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None

    metadata: Dict[str, Any] = {}
    if row['metadata']:
        try:
            metadata = json.loads(row['metadata']) if row['metadata'] else {}
        except Exception:
            metadata = {}

    return {
        'id': row['id'],
        'session_id': row['session_id'],
        'role': row['role'],
        'content': row['content'],
        'timestamp': row['timestamp'],
        'metadata': metadata,
    }


def update_message_content(session_id: str, message_id: int, content: str) -> None:
    conn = db.get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        '''
        UPDATE chat_messages
        SET content = ?, timestamp = ?
        WHERE id = ? AND session_id = ?
        ''',
        (content, now, message_id, session_id),
    )
    conn.commit()
    conn.close()


def update_message_content_and_metadata(
    session_id: str,
    message_id: int,
    content: str,
    metadata: Optional[Dict[str, Any]],
) -> None:
    conn = db.get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        '''
        UPDATE chat_messages
        SET content = ?, metadata = ?, timestamp = ?
        WHERE id = ? AND session_id = ?
        ''',
        (
            content,
            json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            now,
            message_id,
            session_id,
        ),
    )
    conn.commit()
    conn.close()


def list_dialogue_messages_between(session_id: str, start_message_id: int, end_message_id: int) -> List[Dict[str, Any]]:
    return db.get_dialogue_messages_between(session_id, start_message_id, end_message_id)


def list_dialogue_messages_after(session_id: str, after_message_id: int) -> List[Dict[str, Any]]:
    return db.get_dialogue_messages_after(session_id, after_message_id)


def get_latest_assistant_message_id(session_id: str) -> Optional[int]:
    return db.get_latest_assistant_message_id(session_id)


def save_message_attachment(
    message_id: int,
    name: Optional[str],
    mime: Optional[str],
    data: bytes,
    width: Optional[int] = None,
    height: Optional[int] = None,
    size: Optional[int] = None,
) -> Dict[str, Any]:
    return db.save_message_attachment(message_id, name, mime, data, width, height, size)


def get_attachment(attachment_id: int) -> Optional[Dict[str, Any]]:
    return db.get_attachment(attachment_id)


def save_agent_step(
    message_id: int,
    step_type: str,
    content: str,
    sequence: int,
    metadata: Optional[Dict[str, Any]] = None,
    agent_profile: Optional[str] = None
) -> int:
    return db.save_agent_step(message_id, step_type, content, sequence, metadata or {}, agent_profile=agent_profile)


def list_agent_steps(session_id: str) -> List[Dict[str, Any]]:
    return db.get_session_agent_steps(session_id)


def list_agent_steps_for_messages(session_id: str, message_ids: List[int]) -> List[Dict[str, Any]]:
    return db.get_session_agent_steps_for_messages(session_id, message_ids)


def list_agent_steps_for_profile_after(
    session_id: str,
    agent_profile: str,
    after_step_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    return db.get_agent_steps_for_profile_after(session_id, agent_profile, after_step_id)


def get_file_snapshot(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return db.get_file_snapshot(session_id, message_id)


def create_file_snapshot(session_id: str, message_id: int, tree_hash: str, work_path: str) -> Optional[Dict[str, Any]]:
    return db.create_file_snapshot(session_id, message_id, tree_hash, work_path)


def get_snapshot_for_rollback(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return db.get_snapshot_for_rollback(session_id, message_id)


def delete_file_snapshots_from(session_id: str, message_id: int) -> None:
    db.delete_file_snapshots_from(session_id, message_id)


def save_llm_call(
    session_id: str,
    message_id: Optional[int],
    agent_type: Optional[str],
    agent_profile: Optional[str],
    iteration: Optional[int],
    stream: bool,
    api_profile: str,
    api_format: str,
    model: str,
    request_json: Optional[Dict[str, Any]],
    response_json: Optional[Dict[str, Any]],
    response_text: Optional[str],
    processed_json: Optional[Dict[str, Any]],
) -> int:
    return db.save_llm_call(
        session_id=session_id,
        message_id=message_id,
        agent_type=agent_type,
        agent_profile=agent_profile,
        iteration=iteration,
        stream=stream,
        api_profile=api_profile,
        api_format=api_format,
        model=model,
        request_json=request_json,
        response_json=response_json,
        response_text=response_text,
        processed_json=processed_json,
    )


def update_llm_call_processed(llm_call_id: int, processed_json: Dict[str, Any]) -> None:
    db.update_llm_call_processed(llm_call_id, processed_json)


def list_llm_calls(session_id: str) -> List[Dict[str, Any]]:
    return db.get_session_llm_calls(session_id)


def get_latest_llm_call_id(session_id: str) -> Optional[int]:
    return db.get_latest_llm_call_id(session_id)


def save_session_tool_call_history(
    session_id: str,
    tool_name: str,
    success: bool,
    message_id: Optional[int] = None,
    agent_type: Optional[str] = None,
    agent_profile: Optional[str] = None,
    iteration: Optional[int] = None,
    failure_reason: Optional[str] = None,
) -> int:
    return db.save_session_tool_call_history(
        session_id=session_id,
        message_id=message_id,
        agent_type=agent_type,
        agent_profile=agent_profile,
        iteration=iteration,
        tool_name=tool_name,
        success=success,
        failure_reason=failure_reason,
    )


def get_tool_stats(session_id: str) -> Dict[str, Any]:
    return db.get_session_tool_stats(session_id)


def save_tool_call(
    message_id: int,
    tool_name: str,
    tool_input: str,
    tool_output: str,
    agent_profile: Optional[str] = None
) -> int:
    return db.save_tool_call(message_id, tool_name, tool_input, tool_output, agent_profile=agent_profile)


def get_llm_call_metas_after(session_id: str, after_id: int = 0) -> List[Dict[str, Any]]:
    return db.get_llm_call_metas_after(session_id, after_id)


def get_max_message_id_for_llm_call(session_id: str, call_id: int) -> Optional[int]:
    return db.get_max_message_id_for_llm_call(session_id, call_id)


def rollback_session(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return db.rollback_session(session_id, message_id)


def update_session_context(
    session_id: str,
    context_summary: Optional[str],
    last_compressed_llm_call_id: Optional[int],
) -> None:
    db.update_session_context(session_id, context_summary, last_compressed_llm_call_id)


def get_agent_private_context(session_id: str, agent_profile: str) -> Optional[Dict[str, Any]]:
    return db.get_agent_private_context(session_id, agent_profile)


def upsert_agent_private_context(
    session_id: str,
    agent_profile: str,
    context_summary: Optional[str],
    last_compressed_step_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    return db.upsert_agent_private_context(
        session_id,
        agent_profile,
        context_summary,
        last_compressed_step_id,
    )
