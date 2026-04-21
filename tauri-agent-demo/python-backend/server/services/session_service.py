import time
import uuid
from typing import Optional

from fastapi import HTTPException, Query

from ghost_snapshot import restore_snapshot
from models import BranchSessionCreateRequest, BranchSessionCreateResponse, ChatSessionCreate, ChatSessionUpdate, RollbackRequest
from repositories import config_repository, session_repository
from ..session_support import build_copy_title, cleanup_spawned_subagents, schedule_ast_scan
from tools.pty_manager import get_pty_manager


def get_sessions():
    return session_repository.list_sessions()


def get_session(session_id: str, include_count: bool = Query(True)):
    t0 = time.perf_counter()
    session = session_repository.get_session(session_id, include_count=include_count)
    t1 = time.perf_counter()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    print("[Session Fetch] session=%s include_count=%s db=%.1fms" % (session_id, include_count, (t1 - t0) * 1000))
    return session


def create_session(session: ChatSessionCreate):
    config = config_repository.get_config(session.config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    created = session_repository.create_session(session)
    schedule_ast_scan(created.work_path)
    return created


def update_session(session_id: str, update: ChatSessionUpdate):
    if update.config_id is not None and not config_repository.get_config(update.config_id):
        raise HTTPException(status_code=404, detail="Config not found")
    session = session_repository.update_session(session_id, update)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if update.work_path is not None:
        schedule_ast_scan(session.work_path)
    return session


def copy_session(session_id: str):
    source = session_repository.get_session(session_id, include_count=False)
    if not source:
        raise HTTPException(status_code=404, detail="Session not found")
    title = build_copy_title(source.title, session_repository.list_sessions())
    created = session_repository.copy_session(session_id, title)
    if not created:
        raise HTTPException(status_code=404, detail="Session not found")
    schedule_ast_scan(created.work_path)
    return created


def create_branch_session(session_id: str, request: BranchSessionCreateRequest):
    session = session_repository.get_session(session_id, include_count=False)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    selection_start = int(request.selection_start)
    selection_end = int(request.selection_end)
    if selection_start < 0 or selection_end <= selection_start:
        raise HTTPException(status_code=400, detail="Invalid selection range")

    selected_text = str(request.selected_text or "")
    if not selected_text.strip():
        raise HTTPException(status_code=400, detail="Selected text is empty")

    source_message = session_repository.get_message_details(session_id, request.source_message_id)
    if not source_message:
        raise HTTPException(status_code=404, detail="Source message not found")
    if source_message.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="Source message must be an assistant message")

    source_steps = session_repository.list_agent_steps(session_id, [request.source_message_id])
    matched_step = next(
        (
            step for step in source_steps
            if int(step.get("sequence") if step.get("sequence") is not None else -1) == int(request.source_step_sequence)
        ),
        None,
    )
    if not matched_step or str(matched_step.get("step_type") or "") != "answer":
        raise HTTPException(status_code=400, detail="Source step must be a final answer step")

    raw_metadata = source_message.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    branch_links = metadata.get("branch_links")
    normalized_links = [item for item in branch_links if isinstance(item, dict)] if isinstance(branch_links, list) else []

    for item in normalized_links:
        try:
            item_start = int(item.get("start_offset"))
            item_end = int(item.get("end_offset"))
            item_step = int(item.get("step_sequence"))
        except (TypeError, ValueError):
            continue
        if item_step != int(request.source_step_sequence) or item_start != selection_start or item_end != selection_end:
            continue
        child_session_id = str(item.get("child_session_id") or "").strip()
        if not child_session_id:
            continue
        existing = session_repository.get_session(child_session_id, include_count=False)
        if existing and getattr(existing, "session_kind", "regular") == "branch":
            return BranchSessionCreateResponse(
                branch_session=existing,
                source_message_id=request.source_message_id,
                source_message_metadata=metadata,
                existing=True,
            )

    created = session_repository.create_branch_session(
        session_id,
        request.source_message_id,
        title="Branch Question",
    )
    if not created:
        raise HTTPException(status_code=404, detail="Failed to create branch session")

    new_link = {
        "id": str(uuid.uuid4()),
        "child_session_id": created.id,
        "step_sequence": int(request.source_step_sequence),
        "start_offset": selection_start,
        "end_offset": selection_end,
        "selected_text": selected_text,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    next_metadata = {**metadata, "branch_links": [*normalized_links, new_link]}
    session_repository.update_message_content_and_metadata(
        session_id,
        request.source_message_id,
        str(source_message.get("content") or ""),
        next_metadata,
    )
    schedule_ast_scan(created.work_path)
    updated_message = session_repository.get_message_details(session_id, request.source_message_id)
    updated_metadata = updated_message.get("metadata") if isinstance(updated_message, dict) else next_metadata
    return BranchSessionCreateResponse(
        branch_session=created,
        source_message_id=request.source_message_id,
        source_message_metadata=updated_metadata if isinstance(updated_metadata, dict) else next_metadata,
        existing=False,
    )


def delete_session(session_id: str):
    if not session_repository.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        get_pty_manager().close_session(session_id)
    except Exception:
        pass
    return {"success": True}


def get_session_messages(session_id: str, limit: Optional[int] = None, before_id: Optional[int] = None):
    t0 = time.perf_counter()
    session = session_repository.get_session(session_id, include_count=False)
    t1 = time.perf_counter()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if before_id is not None:
        fetch_limit = limit or 50
        messages = session_repository.list_messages_before(session_id, before_id, fetch_limit)
    else:
        messages = session_repository.list_messages(session_id, limit)
    t2 = time.perf_counter()
    print(
        "[Session Fetch] messages session=%s lookup=%.1fms db=%.1fms total=%.1fms count=%s limit=%s before_id=%s"
        % (
            session_id,
            (t1 - t0) * 1000,
            (t2 - t1) * 1000,
            (t2 - t0) * 1000,
            len(messages),
            limit if limit is not None else "none",
            before_id if before_id is not None else "none",
        )
    )
    return messages


def get_session_llm_calls(session_id: str):
    t0 = time.perf_counter()
    session = session_repository.get_session(session_id)
    t1 = time.perf_counter()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    calls = session_repository.list_llm_calls(session_id)
    t2 = time.perf_counter()
    print("[Session Fetch] llm_calls session=%s lookup=%.1fms db=%.1fms total=%.1fms count=%s" % (session_id, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000, len(calls)))
    return calls


def get_session_tool_stats(session_id: str):
    t0 = time.perf_counter()
    session = session_repository.get_session(session_id)
    t1 = time.perf_counter()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    stats = session_repository.get_tool_stats(session_id)
    t2 = time.perf_counter()
    print("[Session Fetch] tool_stats session=%s lookup=%.1fms db=%.1fms total=%.1fms total_calls=%s tools=%s" % (session_id, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000, stats.get("total_calls", 0), len(stats.get("tools", []) or [])))
    return stats


def get_session_agent_steps(session_id: str, message_ids: Optional[str] = None):
    t0 = time.perf_counter()
    session = session_repository.get_session(session_id)
    t1 = time.perf_counter()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    ids = [int(item) for item in message_ids.split(",") if item.strip().isdigit()] if message_ids else None
    steps = session_repository.list_agent_steps(session_id, ids)
    t2 = time.perf_counter()
    print("[Session Fetch] agent_steps session=%s lookup=%.1fms db=%.1fms total=%.1fms count=%s filter=%s" % (session_id, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000, len(steps), "message_ids" if message_ids else "all"))
    return steps


async def rollback_session(session_id: str, request: RollbackRequest):
    target = session_repository.get_message(session_id, request.message_id)
    if not target:
        raise HTTPException(status_code=404, detail="Message not found in session")
    if target.get("role") != "user":
        raise HTTPException(status_code=400, detail="Rollback target must be a user message.")

    try:
        child_session_ids = session_repository.list_spawned_subagent_child_sessions(
            session_id,
            min_message_id=request.message_id,
        )
        cleanup_spawned_subagents(child_session_ids)
    except Exception as exc:
        print(f"[Subagent Cleanup] Rollback cleanup failed: {exc}")

    snapshot = session_repository.get_snapshot_for_rollback(session_id, request.message_id)
    if snapshot:
        try:
            restore_snapshot(snapshot.get("tree_hash"), snapshot.get("work_path"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Snapshot restore failed: {str(exc)}")

    result = session_repository.rollback_session(session_id, request.message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found in session")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    if snapshot:
        try:
            session_repository.delete_file_snapshots_from(session_id, request.message_id)
        except Exception:
            pass
        result["snapshot_restored"] = True
    else:
        result["snapshot_restored"] = False

    try:
        session_repository.update_session_context(session_id, None, None)
    except Exception:
        pass

    return result
