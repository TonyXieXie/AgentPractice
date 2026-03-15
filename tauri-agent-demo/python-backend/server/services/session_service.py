import time
from typing import Optional

from fastapi import HTTPException, Query

from agent_team import AgentTeam
from app_config import get_app_config
from ghost_snapshot import restore_snapshot
from models import ChatSessionCreate, ChatSessionUpdate, RollbackRequest
from repositories import config_repository, session_repository, team_repository
from ..session_support import build_copy_title, cleanup_spawned_subagents, schedule_ast_scan
from tools.pty_manager import get_pty_manager


def _model_dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


def _model_fields_set(model):
    fields = getattr(model, "model_fields_set", None)
    if fields is not None:
        return set(fields)
    return set(getattr(model, "__fields_set__", set()) or set())


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
    team = AgentTeam(get_app_config())
    try:
        selection = team.resolve_session_selection(
            requested_profile=session.agent_profile,
            requested_team_id=session.agent_team_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session = ChatSessionCreate(
        **{
            **_model_dump(session),
            "agent_profile": selection.get("agent_profile"),
            "agent_team_id": selection.get("agent_team_id"),
            "team_id": None,
            "role_key": None,
            "parent_session_id": None,
        }
    )
    created = session_repository.create_session(session)
    schedule_ast_scan(created.work_path)
    return created


def update_session(session_id: str, update: ChatSessionUpdate):
    if update.config_id is not None and not config_repository.get_config(update.config_id):
        raise HTTPException(status_code=404, detail="Config not found")
    selection_fields = _model_fields_set(update)
    internal_runtime_fields = {"team_id", "role_key", "parent_session_id"}
    touched_internal_fields = sorted(field for field in internal_runtime_fields if field in selection_fields)
    if touched_internal_fields:
        field_list = ", ".join(touched_internal_fields)
        raise HTTPException(
            status_code=400,
            detail=f"{field_list} is managed internally and cannot be updated through the session API.",
        )
    if "agent_profile" in selection_fields or "agent_team_id" in selection_fields:
        current = session_repository.get_session(session_id, include_count=False)
        if not current:
            raise HTTPException(status_code=404, detail="Session not found")
        if getattr(current, "team_id", None):
            requested_profile = update.agent_profile if "agent_profile" in selection_fields else getattr(current, "agent_profile", None)
            requested_team_id = update.agent_team_id if "agent_team_id" in selection_fields else getattr(current, "agent_team_id", None)
            if (
                requested_profile != getattr(current, "agent_profile", None)
                or requested_team_id != getattr(current, "agent_team_id", None)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Runtime team sessions have a fixed agent binding and cannot switch agent/team selection.",
                )
        team = AgentTeam(get_app_config())
        try:
            selection = team.resolve_session_selection(
                session_profile=getattr(current, "agent_profile", None),
                session_team_id=getattr(current, "agent_team_id", None),
                requested_profile=update.agent_profile,
                requested_team_id=update.agent_team_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        update = ChatSessionUpdate(
            **{
                **_model_dump(update),
                "agent_profile": selection.get("agent_profile"),
                "agent_team_id": selection.get("agent_team_id"),
            }
        )
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

    session = session_repository.get_session(session_id, include_count=False)
    runtime_team_id = getattr(session, "team_id", None) if session else None
    target_timestamp = str(target.get("timestamp") or "").strip()
    if runtime_team_id and target_timestamp and team_repository.has_handoff_events_since(runtime_team_id, target_timestamp):
        raise HTTPException(
            status_code=400,
            detail="Rollback is blocked for runtime team sessions after handoff events have been recorded.",
        )

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
