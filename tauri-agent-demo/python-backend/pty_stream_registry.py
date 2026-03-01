import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_KEEPALIVE_SEC = 15
DEFAULT_MAX_EVENTS = 4000
DEFAULT_TTL_SEC = 3600


def _get_int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


class PtySessionStreamState:
    def __init__(
        self,
        session_id: str,
        keepalive_sec: int,
        max_events: int,
        ttl_sec: int,
    ) -> None:
        self.session_id = str(session_id or "").strip()
        self.stream_id = uuid.uuid4().hex[:12]
        self.keepalive_sec = int(keepalive_sec or DEFAULT_KEEPALIVE_SEC)
        self.max_events = int(max_events or DEFAULT_MAX_EVENTS)
        self.ttl_sec = int(ttl_sec or DEFAULT_TTL_SEC)
        self.created_at = time.monotonic()
        self.last_activity = self.created_at
        self._seq = 0
        self._events: List[Tuple[int, str]] = []
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition()

    async def emit(self, payload: Dict[str, Any]) -> int:
        if not self.session_id:
            return 0
        async with self._lock:
            pty_seq = None
            try:
                pty_seq = int((payload or {}).get("seq")) if (payload or {}).get("seq") is not None else None
            except (TypeError, ValueError):
                pty_seq = None
            self._seq += 1
            event_payload = dict(payload or {})
            event_payload.setdefault("session_id", self.session_id)
            if pty_seq is not None and "pty_seq" not in event_payload:
                event_payload["pty_seq"] = pty_seq
            event_payload["stream_seq"] = self._seq
            event_payload["seq"] = self._seq
            encoded = json.dumps(event_payload, ensure_ascii=False)
            self._events.append((self._seq, encoded))
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            self.last_activity = time.monotonic()
        async with self._cond:
            self._cond.notify_all()
        return self._seq

    async def _snapshot_since(self, last_seq: int) -> Tuple[List[Tuple[int, str]], int, Optional[int]]:
        async with self._lock:
            events = [(seq, data) for seq, data in self._events if seq > last_seq]
            latest_seq = self._seq
            oldest_seq = self._events[0][0] if self._events else None
        return events, latest_seq, oldest_seq

    async def stream(self, last_seq: Optional[int]) -> Any:
        cursor = int(last_seq or 0)
        init_payload = {
            "stream_id": self.stream_id,
            "session_id": self.session_id,
        }
        yield f"data: {json.dumps(init_payload, ensure_ascii=False)}\n\n"
        try:
            while True:
                events, latest_seq, oldest_seq = await self._snapshot_since(cursor)

                if oldest_seq is not None and cursor > 0 and cursor < (oldest_seq - 1):
                    resync_payload = {
                        "event": "pty_resync_required",
                        "session_id": self.session_id,
                        "reason": "seq_gap",
                        "seq": latest_seq,
                    }
                    yield f"data: {json.dumps(resync_payload, ensure_ascii=False)}\n\n"
                    cursor = max(cursor, oldest_seq - 1)
                    events, latest_seq, oldest_seq = await self._snapshot_since(cursor)

                for seq, data in events:
                    cursor = seq
                    yield f"data: {data}\n\n"

                try:
                    async with self._cond:
                        await asyncio.wait_for(self._cond.wait(), timeout=self.keepalive_sec)
                except asyncio.TimeoutError:
                    yield ":\n\n"
        except asyncio.CancelledError:
            return


class PtyStreamRegistry:
    def __init__(self) -> None:
        self._states: Dict[str, PtySessionStreamState] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._max_events = _get_int_env("PTY_STREAM_MAX_EVENTS", DEFAULT_MAX_EVENTS)
        self._ttl_sec = _get_int_env("PTY_STREAM_TTL_SEC", DEFAULT_TTL_SEC)
        self._cleanup_interval_sec = 30.0
        self._last_cleanup = 0.0

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def get(self, session_id: str) -> Optional[PtySessionStreamState]:
        await self._maybe_cleanup()
        sid = str(session_id or "").strip()
        if not sid:
            return None
        async with self._lock:
            return self._states.get(sid)

    async def ensure(self, session_id: str, keepalive_sec: int = DEFAULT_KEEPALIVE_SEC) -> PtySessionStreamState:
        await self._maybe_cleanup()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("Missing session_id")
        async with self._lock:
            state = self._states.get(sid)
            if state is None:
                state = PtySessionStreamState(
                    session_id=sid,
                    keepalive_sec=keepalive_sec,
                    max_events=self._max_events,
                    ttl_sec=self._ttl_sec,
                )
                self._states[sid] = state
            else:
                state.keepalive_sec = int(keepalive_sec or state.keepalive_sec or DEFAULT_KEEPALIVE_SEC)
            return state

    async def emit(self, session_id: str, payload: Dict[str, Any]) -> int:
        sid = str(session_id or "").strip()
        if not sid:
            return 0
        state = await self.ensure(sid, keepalive_sec=DEFAULT_KEEPALIVE_SEC)
        return await state.emit(payload)

    def emit_threadsafe(self, session_id: str, payload: Dict[str, Any]) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            loop.create_task(self.emit(session_id, payload))
            return
        asyncio.run_coroutine_threadsafe(self.emit(session_id, payload), loop)

    async def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval_sec:
            return
        self._last_cleanup = now
        async with self._lock:
            stale: List[str] = []
            for sid, state in self._states.items():
                if now - state.last_activity > state.ttl_sec:
                    stale.append(sid)
            for sid in stale:
                self._states.pop(sid, None)


_PTY_STREAM_REGISTRY = PtyStreamRegistry()


def get_pty_stream_registry() -> PtyStreamRegistry:
    return _PTY_STREAM_REGISTRY
