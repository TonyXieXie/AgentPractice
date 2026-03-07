import json
import os

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from repositories import session_repository
from tools.base import ToolRegistry
from tools.config import get_tool_config, get_tool_config_path
from app_config import get_app_config_path
from ..attachment_utils import build_thumbnail
from ..runtime_state import WS_HUB


def read_local_file(path: str, max_bytes: int = 2_000_000):
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    safe_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        size = os.path.getsize(safe_path)
        if max_bytes and size > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        with open(safe_path, "rb") as file:
            raw = file.read(max_bytes + 1 if max_bytes else None)
        if max_bytes and len(raw) > max_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        return {"content": raw.decode("utf-8", errors="replace")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}")


def local_file_exists(path: str):
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    safe_path = os.path.abspath(os.path.expanduser(path))
    return {"exists": os.path.isfile(safe_path)}


def read_root():
    return {"status": "FastAPI is running!", "version": "2.2", "app_config": True}


def debug_info():
    tool_config = get_tool_config()
    return {
        "file": __file__,
        "cwd": os.getcwd(),
        "routes": [
            "/local-file",
            "/local-file-exists",
            "/",
            "/__debug/info",
            "/ws",
        ],
        "tool_config_path": get_tool_config_path(),
        "app_config_path": get_app_config_path(),
        "tools_enabled": tool_config.get("enabled", {}),
        "tool_names": [tool.name for tool in ToolRegistry.get_all()],
    }


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conn = await WS_HUB.register(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                continue
            msg_type = payload.get("type")
            if msg_type == "subscribe":
                session_ids = payload.get("session_ids") or []
                if isinstance(session_ids, str):
                    session_ids = [session_ids]
                await WS_HUB.subscribe(conn, session_ids)
            elif msg_type == "unsubscribe":
                session_ids = payload.get("session_ids") or []
                if isinstance(session_ids, str):
                    session_ids = [session_ids]
                await WS_HUB.unsubscribe(conn, session_ids)
            elif msg_type == "ping":
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        await WS_HUB.unregister(conn)


def get_attachment(attachment_id: int, thumbnail: bool = False, max_size: int = 360):
    attachment = session_repository.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    data = attachment.get("data") or b""
    if isinstance(data, memoryview):
        data = data.tobytes()
    mime = attachment.get("mime") or "application/octet-stream"
    if thumbnail:
        thumb = build_thumbnail(data, max_size=max_size)
        if thumb:
            mime, data = thumb
    return Response(content=data, media_type=mime)
