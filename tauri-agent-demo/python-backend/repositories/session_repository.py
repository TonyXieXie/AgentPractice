from typing import Any, Dict, List, Optional

from database import db
from models import ChatMessage, ChatSession, ChatSessionCreate, ChatSessionUpdate, GraphNodeRun, GraphRun

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


def update_context_estimate(session_id: str, estimate: Optional[Dict[str, Any]]) -> Optional[ChatSession]:
    return db.update_session_context_estimate(session_id, estimate)


def create_graph_run(graph_run: GraphRun) -> GraphRun:
    return db.create_graph_run(graph_run)


def get_graph_run(graph_run_id: str) -> Optional[GraphRun]:
    return db.get_graph_run(graph_run_id)


def get_latest_incomplete_graph_run(session_id: str, request_text: Optional[str] = None) -> Optional[GraphRun]:
    return db.get_latest_incomplete_graph_run(session_id, request_text=request_text)


def update_graph_run(
    graph_run_id: str,
    *,
    state_json: Optional[Any] = None,
    active_node_id: Optional[str] = None,
    status: Optional[str] = None,
    hop_count: Optional[int] = None,
    last_result: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
    completed_at: Optional[str] = None,
) -> Optional[GraphRun]:
    return db.update_graph_run(
        graph_run_id,
        state_json=state_json,
        active_node_id=active_node_id,
        status=status,
        hop_count=hop_count,
        last_result=last_result,
        error=error,
        completed_at=completed_at,
    )


def create_graph_node_run(node_run: GraphNodeRun) -> GraphNodeRun:
    return db.create_graph_node_run(node_run)


def get_graph_node_run(graph_node_run_id: str) -> Optional[GraphNodeRun]:
    return db.get_graph_node_run(graph_node_run_id)


def update_graph_node_run(
    graph_node_run_id: str,
    *,
    status: Optional[str] = None,
    output_json: Optional[Dict[str, Any]] = None,
    state_patch_json: Optional[Dict[str, Any]] = None,
    error_json: Optional[Dict[str, Any]] = None,
    completed_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Optional[GraphNodeRun]:
    return db.update_graph_node_run(
        graph_node_run_id,
        status=status,
        output_json=output_json,
        state_patch_json=state_patch_json,
        error_json=error_json,
        completed_at=completed_at,
        duration_ms=duration_ms,
    )


def list_graph_node_runs(graph_run_id: str) -> List[GraphNodeRun]:
    return db.get_graph_node_runs(graph_run_id)
