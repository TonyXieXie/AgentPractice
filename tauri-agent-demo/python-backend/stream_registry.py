import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_KEEPALIVE_SEC = 15
DEFAULT_MAX_EVENTS = 2000
DEFAULT_TTL_SEC = 600


def _get_int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


class StreamState:
    def __init__(
        self,
        stream_id: str,
        keepalive_sec: int,
        max_events: int,
        ttl_sec: int
    ) -> None:
        self.stream_id = stream_id
        self.keepalive_sec = int(keepalive_sec or DEFAULT_KEEPALIVE_SEC)
        self.max_events = int(max_events or DEFAULT_MAX_EVENTS)
        self.ttl_sec = int(ttl_sec or DEFAULT_TTL_SEC)
        self._seq = 0
        self._events: List[Tuple[int, str]] = []
        self._init_payload: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition()
        self._done = False
        self._done_at: Optional[float] = None
        self.created_at = time.monotonic()
        self.last_activity = self.created_at

    @property
    def done(self) -> bool:
        return self._done

    @property
    def done_at(self) -> Optional[float]:
        return self._done_at

    async def emit(self, payload: Dict[str, Any]) -> int:
        async with self._lock:
            self._seq += 1
            payload = dict(payload)
            payload["seq"] = self._seq
            encoded = json.dumps(payload, ensure_ascii=False)
            self._events.append((self._seq, encoded))
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            self.last_activity = time.monotonic()
        async with self._cond:
            self._cond.notify_all()
        return self._seq

    async def set_init_payload(self, payload: Dict[str, Any]) -> None:
        if not payload:
            return
        async with self._lock:
            if self._init_payload is not None:
                return
            clean = dict(payload)
            clean.pop("seq", None)
            self._init_payload = clean

    async def mark_done(self) -> None:
        self._done = True
        self._done_at = time.monotonic()
        async with self._cond:
            self._cond.notify_all()

    async def _snapshot_since(self, last_seq: int) -> Tuple[List[Tuple[int, str]], int, bool]:
        async with self._lock:
            events = [(seq, data) for seq, data in self._events if seq > last_seq]
            latest_seq = self._seq
            done = self._done
        return events, latest_seq, done

    async def stream(self, last_seq: Optional[int]) -> Any:
        cursor = int(last_seq or 0)
        if cursor > 0:
            init_payload = None
            async with self._lock:
                if self._init_payload:
                    init_payload = dict(self._init_payload)
            if init_payload:
                encoded = json.dumps(init_payload, ensure_ascii=False)
                yield f"data: {encoded}\n\n"
        while True:
            events, latest_seq, done = await self._snapshot_since(cursor)
            for seq, data in events:
                cursor = seq
                yield f"data: {data}\n\n"
            if done and cursor >= latest_seq:
                return
            try:
                async with self._cond:
                    await asyncio.wait_for(self._cond.wait(), timeout=self.keepalive_sec)
            except asyncio.TimeoutError:
                yield ":\n\n"


class StreamRegistry:
    def __init__(self) -> None:
        self._streams: Dict[str, StreamState] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = 0.0
        self._cleanup_interval_sec = 30.0
        self._ttl_sec = _get_int_env("AGENT_STREAM_TTL_SEC", DEFAULT_TTL_SEC)
        self._max_events = _get_int_env("AGENT_STREAM_MAX_EVENTS", DEFAULT_MAX_EVENTS)

    async def create(self, keepalive_sec: int) -> StreamState:
        await self._maybe_cleanup()
        stream_id = uuid.uuid4().hex[:12]
        state = StreamState(
            stream_id=stream_id,
            keepalive_sec=keepalive_sec,
            max_events=self._max_events,
            ttl_sec=self._ttl_sec
        )
        async with self._lock:
            self._streams[stream_id] = state
        return state

    async def get(self, stream_id: str) -> Optional[StreamState]:
        await self._maybe_cleanup()
        async with self._lock:
            return self._streams.get(stream_id)

    async def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval_sec:
            return
        self._last_cleanup = now
        async with self._lock:
            to_drop = []
            for stream_id, state in self._streams.items():
                if not state.done:
                    continue
                done_at = state.done_at
                if done_at is None:
                    continue
                if now - done_at > state.ttl_sec:
                    to_drop.append(stream_id)
            for stream_id in to_drop:
                self._streams.pop(stream_id, None)


_STREAM_REGISTRY = StreamRegistry()


def get_stream_registry() -> StreamRegistry:
    return _STREAM_REGISTRY
