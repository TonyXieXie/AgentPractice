import os

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from tools.config import get_tool_config
from tools.pty_manager import get_pty_manager
from ..contracts import PtyCloseRequest, PtyReadRequest, PtySendRequest, PtyStreamRequest
from ..runtime_state import PTY_STREAM_REGISTRY


def _resolve_pty_stream_keepalive_sec() -> int:
    keepalive_sec = 15
    try:
        keepalive_sec = int(os.getenv("PTY_STREAM_KEEPALIVE_SEC", "15") or "15")
    except (TypeError, ValueError):
        keepalive_sec = 15
    if keepalive_sec < 5:
        keepalive_sec = 5
    return keepalive_sec


def _normalize_windows_pty_input(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r")


async def stream_pty(request: PtyStreamRequest):
    session_id = str(request.session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    keepalive_sec = _resolve_pty_stream_keepalive_sec()
    last_seq = int(request.last_seq or 0)
    if last_seq < 0:
        last_seq = 0

    if request.resume and request.stream_id:
        state = await PTY_STREAM_REGISTRY.get(session_id)
        if not state or state.stream_id != request.stream_id:
            raise HTTPException(status_code=404, detail="PTY stream not found")
    else:
        state = await PTY_STREAM_REGISTRY.ensure(session_id, keepalive_sec=keepalive_sec)

    return StreamingResponse(
        state.stream(last_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def list_ptys(session_id: str, include_exited: bool = True, max_exited: int = 8):
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    manager = get_pty_manager()
    items = manager.list(session_id)
    running = [item for item in items if item.status == "running"]
    exited = [item for item in items if item.status != "running"]
    running.sort(key=lambda item: item.created_at, reverse=True)
    exited.sort(key=lambda item: item.created_at, reverse=True)
    if not include_exited:
        merged = running
    else:
        try:
            max_exited = int(max_exited)
        except (TypeError, ValueError):
            max_exited = 8
        if max_exited < 0:
            max_exited = 0
        merged = running + exited[:max_exited]
    payload = []
    for item in merged:
        snapshot = item.get_snapshot() if hasattr(item, "get_snapshot") else {}
        payload.append({
            "pty_id": item.id,
            "status": item.status,
            "pty": item.pty_enabled,
            "exit_code": item.exit_code,
            "command": item.command,
            "pty_mode": getattr(item, "pty_mode", "ephemeral"),
            "waiting_input": bool(getattr(item, "waiting_input", False)),
            "wait_reason": getattr(item, "wait_reason", None),
            "created_at": item.created_at,
            "idle_timeout": item.idle_timeout_ms,
            "buffer_size": item.buffer_size,
            "last_output_at": item.last_output_at,
            "seq": snapshot.get("seq"),
            "screen_hash": snapshot.get("screen_hash"),
        })
    return {"ok": True, "items": payload}


def read_pty(request: PtyReadRequest):
    manager = get_pty_manager()
    proc = manager.get(request.session_id, request.pty_id)
    if not proc:
        raise HTTPException(status_code=404, detail="PTY not found")
    max_output = request.max_output
    if max_output is None:
        try:
            max_output = int(get_tool_config().get("shell", {}).get("max_output", 20000))
        except (TypeError, ValueError):
            max_output = 20000
    try:
        max_output = int(max_output)
    except (TypeError, ValueError):
        max_output = 20000
    chunk, cursor, reset = proc.read(request.cursor, max_output)
    snapshot = proc.get_snapshot() if hasattr(proc, "get_snapshot") else {}
    response = {
        "ok": True,
        "pty_id": proc.id,
        "status": proc.status,
        "pty": proc.pty_enabled,
        "exit_code": proc.exit_code,
        "command": proc.command,
        "pty_mode": getattr(proc, "pty_mode", "ephemeral"),
        "pty_live": bool(getattr(proc, "pty_live", False)),
        "cursor": cursor,
        "reset": reset,
        "chunk": chunk,
        "waiting_input": bool(getattr(proc, "waiting_input", False)),
        "wait_reason": getattr(proc, "wait_reason", None),
        "screen_text": snapshot.get("screen_text"),
        "screen_hash": snapshot.get("screen_hash"),
        "seq": snapshot.get("seq"),
    }
    if getattr(proc, "pty_message_id", None) is not None:
        response["pty_message_id"] = proc.pty_message_id
    return response


def send_pty(request: PtySendRequest):
    manager = get_pty_manager()
    proc = manager.get(request.session_id, request.pty_id)
    if not proc:
        raise HTTPException(status_code=404, detail="PTY not found")
    payload = request.input or ""
    if os.name == "nt" and payload:
        payload = _normalize_windows_pty_input(payload)
    data = payload.encode("utf-8", errors="replace")
    written = proc.write(data)
    if hasattr(proc, "update_waiting_state"):
        proc.update_waiting_state(False, None)
    else:
        proc.waiting_input = False
        proc.wait_reason = None
    return {"ok": True, "pty_id": proc.id, "bytes_written": written}


def close_pty(request: PtyCloseRequest):
    manager = get_pty_manager()
    closed = manager.close(request.session_id, request.pty_id)
    return {"ok": closed, "pty_id": request.pty_id}
