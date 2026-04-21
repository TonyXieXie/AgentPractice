from typing import Any, Dict, List, Optional

from database import db
from models import ChatMessage, ChatSession, ChatSessionCreate, ChatSessionUpdate

from . import chat_repository


def list_sessions() -> List[ChatSession]:
    return db.get_all_sessions()


def get_session(session_id: str, include_count: bool = True) -> Optional[ChatSession]:
    return db.get_session(session_id, include_count=include_count)


def create_session(session: ChatSessionCreate) -> ChatSession:
    return db.create_session(session)


def update_session(session_id: str, update: ChatSessionUpdate) -> Optional[ChatSession]:
    return db.update_session(session_id, update)


def copy_session(session_id: str, title: Optional[str] = None) -> Optional[ChatSession]:
    return db.copy_session(session_id, title)


def create_branch_session(session_id: str, source_message_id: int, title: Optional[str] = None) -> Optional[ChatSession]:
    return db.create_branch_session(session_id, source_message_id, title)


def delete_session(session_id: str) -> bool:
    return db.delete_session(session_id)


def list_messages(session_id: str, limit: Optional[int] = None) -> List[ChatMessage]:
    return chat_repository.list_messages(session_id, limit)


def list_messages_before(session_id: str, before_id: int, limit: int) -> List[ChatMessage]:
    return chat_repository.list_messages_before(session_id, before_id, limit)


def list_llm_calls(session_id: str) -> List[Dict[str, Any]]:
    return chat_repository.list_llm_calls(session_id)


def get_tool_stats(session_id: str) -> Dict[str, Any]:
    return chat_repository.get_tool_stats(session_id)


def list_agent_steps(session_id: str, message_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    if message_ids:
        return chat_repository.list_agent_steps_for_messages(session_id, message_ids)
    return chat_repository.list_agent_steps(session_id)


def get_attachment(attachment_id: int) -> Optional[Dict[str, Any]]:
    return chat_repository.get_attachment(attachment_id)


def get_message(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return chat_repository.get_message(session_id, message_id)


def get_message_details(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return chat_repository.get_message_details(session_id, message_id)


def list_spawned_subagent_child_sessions(
    session_id: str,
    message_id: Optional[int] = None,
    min_message_id: Optional[int] = None,
) -> List[str]:
    return db.get_spawned_subagent_child_sessions(
        session_id,
        message_id=message_id,
        min_message_id=min_message_id,
    )


def get_snapshot_for_rollback(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return chat_repository.get_snapshot_for_rollback(session_id, message_id)


def delete_file_snapshots_from(session_id: str, message_id: int) -> None:
    chat_repository.delete_file_snapshots_from(session_id, message_id)


def rollback_session(session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
    return chat_repository.rollback_session(session_id, message_id)


def update_session_context(
    session_id: str,
    context_summary: Optional[str],
    last_compressed_llm_call_id: Optional[int],
) -> None:
    chat_repository.update_session_context(session_id, context_summary, last_compressed_llm_call_id)


def update_message_content_and_metadata(
    session_id: str,
    message_id: int,
    content: str,
    metadata: Optional[Dict[str, Any]],
) -> None:
    chat_repository.update_message_content_and_metadata(session_id, message_id, content, metadata)


def update_context_estimate(session_id: str, estimate: Optional[Dict[str, Any]]) -> Optional[ChatSession]:
    return db.update_session_context_estimate(session_id, estimate)
