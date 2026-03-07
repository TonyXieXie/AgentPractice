from typing import List, Optional

from models import ChatSession
from repositories import session_repository
from subagent_runner import cancel_subagent_task, suppress_subagent_parent_notify
from tools.pty_manager import get_pty_manager


def cleanup_spawned_subagents(child_session_ids: List[str]) -> None:
    if not child_session_ids:
        return
    for child_session_id in child_session_ids:
        if not child_session_id:
            continue
        try:
            suppress_subagent_parent_notify(child_session_id)
        except Exception:
            pass
        try:
            cancel_subagent_task(child_session_id)
        except Exception:
            pass
        try:
            get_pty_manager().close_session(child_session_id)
        except Exception:
            pass
        try:
            session_repository.delete_session(child_session_id)
        except Exception as exc:
            print(f"[Subagent Cleanup] Failed to delete child session {child_session_id}: {exc}")


def schedule_ast_scan(work_path: Optional[str]) -> None:
    if not work_path:
        return
    try:
        from ast_index import get_ast_index

        get_ast_index().ensure_root(work_path)
    except Exception:
        pass


def build_copy_title(base_title: str, sessions: List[ChatSession]) -> str:
    existing = {session.title for session in sessions if session.title}
    candidate = f"{base_title} (copy)"
    if candidate not in existing:
        return candidate
    index = 2
    while f"{base_title} (copy {index})" in existing:
        index += 1
    return f"{base_title} (copy {index})"
