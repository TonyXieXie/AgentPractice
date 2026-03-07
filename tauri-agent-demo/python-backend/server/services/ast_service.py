import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app_config import get_app_config
from ast_index import get_ast_index
from ast_settings import get_all_ast_settings, get_ast_settings, update_ast_settings
from code_map import build_code_map_prompt
from repositories import chat_repository, session_repository
from models import AstNotifyRequest, AstRequest, AstSettingsRequest, ChatStopRequest
from stream_control import stream_stop_registry
from tools.base import ToolRegistry
from tools.builtin import register_builtin_tools
from tools.builtin.ast_tools import CodeAstTool
from tools.config import get_tool_config, update_tool_config
from tools.context import reset_tool_context, set_tool_context


async def run_ast(request: AstRequest):
    session = None
    if request.session_id:
        session = session_repository.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    work_path = request.work_path or (session.work_path if session else None)
    token = set_tool_context({
        "shell_unrestricted": False,
        "agent_mode": request.agent_mode or "default",
        "session_id": session.id if session else request.session_id,
        "work_path": work_path,
        "extra_work_paths": request.extra_work_paths,
    })
    try:
        tool = CodeAstTool()
        payload: Dict[str, Any] = {"path": request.path}
        if request.mode:
            payload["mode"] = request.mode
        if request.language:
            payload["language"] = request.language
        if request.extensions:
            payload["extensions"] = request.extensions
        if request.max_files is not None:
            payload["max_files"] = request.max_files
        if request.max_symbols is not None:
            payload["max_symbols"] = request.max_symbols
        if request.max_nodes is not None:
            payload["max_nodes"] = request.max_nodes
        if request.max_depth is not None:
            payload["max_depth"] = request.max_depth
        if request.max_bytes is not None:
            payload["max_bytes"] = request.max_bytes
        if request.include_positions is not None:
            payload["include_positions"] = request.include_positions
        if request.include_text is not None:
            payload["include_text"] = request.include_text

        result_text = await tool.execute(json.dumps(payload))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST tool error: {exc}")
    finally:
        reset_tool_context(token)

    try:
        return json.loads(result_text)
    except Exception:
        return {"ok": False, "error": result_text}


def notify_ast(request: AstNotifyRequest):
    if not request.root:
        raise HTTPException(status_code=400, detail="Missing root")
    paths = request.paths or []
    try:
        updated = get_ast_index().notify_paths(request.root, paths)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST notify failed: {exc}")
    return {"ok": True, "updated": updated}


def get_ast_settings_all_route():
    try:
        data = get_all_ast_settings()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST settings error: {exc}")
    return {"ok": True, **data}


def get_ast_settings_route(root: str):
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    try:
        settings = get_ast_settings(root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST settings error: {exc}")
    return {"ok": True, "root": settings.get("root"), "settings": settings}


def update_ast_settings_route(request: AstSettingsRequest):
    if not request.root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(request.root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    patch = request.dict(exclude={"root"}, exclude_none=True)
    try:
        settings = update_ast_settings(request.root, patch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST settings update failed: {exc}")
    try:
        get_ast_index().ensure_root(request.root)
    except Exception:
        pass
    return {"ok": True, "root": settings.get("root"), "settings": settings}


def get_ast_cache(root: str, path: Optional[str] = None, include_payload: bool = False):
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    try:
        app_config = get_app_config()
        agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        ast_enabled = bool(agent_cfg.get("ast_enabled", True))
        if path:
            payload = get_ast_index().get_file_payload(root, path)
            if isinstance(payload, dict) and not ast_enabled:
                payload.setdefault("disabled", True)
            return payload
        entries = get_ast_index().get_root_entries(root, include_payload=include_payload)
        return {"ok": True, "root": root, "files": entries, "disabled": not ast_enabled}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AST cache error: {exc}")


def get_code_map(session_id: str, root: str):
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    if not root:
        raise HTTPException(status_code=400, detail="Missing root")
    if not Path(root).expanduser().exists():
        raise HTTPException(status_code=404, detail="Root path not found")
    session = session_repository.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        prompt = build_code_map_prompt(session_id, root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Code map error: {exc}")
    return {"ok": True, "prompt": prompt or ""}


def stop_chat(request: ChatStopRequest):
    message_id = request.message_id
    if message_id is None and request.session_id:
        message_id = chat_repository.get_latest_assistant_message_id(request.session_id)
    if message_id is None:
        raise HTTPException(status_code=400, detail="Missing message_id or session_id")
    stopped = stream_stop_registry.stop(int(message_id))
    print(f"[STREAM STOP] message_id={message_id} stopped={stopped}")
    return {"stopped": stopped, "message_id": message_id}


def get_tools_config():
    return get_tool_config()


def set_tools_config(payload: Dict[str, Any]):
    try:
        updated = update_tool_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ToolRegistry.clear()
    register_builtin_tools()
    return updated
