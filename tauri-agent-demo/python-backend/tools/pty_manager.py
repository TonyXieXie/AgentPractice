import os
import threading
import time
import uuid
import hashlib
import re
from typing import Dict, Optional, Tuple, List, Callable, Set, Any

try:
    import pyte
except Exception:
    pyte = None


DEFAULT_BUFFER_SIZE = 2 * 1024 * 1024
MAX_BUFFER_SIZE = 5 * 1024 * 1024
DEFAULT_SCREEN_COLS = 120
DEFAULT_SCREEN_ROWS = 40
DEFAULT_SCREEN_FALLBACK_MAX_CHARS = 30000
DEFAULT_SCREEN_FALLBACK_MAX_LINES = 400
_PTY_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_PTY_ANSI_RE = re.compile(r"[\u001b\u009b][\\[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[@-~]")
_PTY_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PTY_COMPLETION_MARKER_RE = re.compile(r"__PTY_COMPLETION_[A-Za-z0-9_.:-]+__")


def _clamp_buffer_size(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_BUFFER_SIZE
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BUFFER_SIZE
    if size <= 0:
        return DEFAULT_BUFFER_SIZE
    return min(size, MAX_BUFFER_SIZE)


def _decode_output_bytes(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16be", errors="replace")
    probe_len = min(len(data), 2000)
    if probe_len >= 4:
        zero_odd = sum(1 for i in range(1, probe_len, 2) if data[i] == 0)
        zero_even = sum(1 for i in range(0, probe_len, 2) if data[i] == 0)
        if zero_odd > max(10, zero_even * 2):
            return data.decode("utf-16le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if os.name == "nt":
        encodings = []
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            oem_cp = int(kernel32.GetOEMCP() or 0)
            if oem_cp:
                encodings.append(f"cp{oem_cp}")
            ansi_cp = int(kernel32.GetACP() or 0)
            if ansi_cp and ansi_cp != oem_cp:
                encodings.append(f"cp{ansi_cp}")
        except Exception:
            encodings = []
        for enc in encodings:
            try:
                return data.decode(enc)
            except Exception:
                continue
    try:
        import locale
        encoding = locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        encoding = "utf-8"
    return data.decode(encoding, errors="replace")


def sanitize_pty_screen_text(text: Optional[str]) -> str:
    value = str(text or "")
    if not value:
        return ""
    value = _PTY_COMPLETION_MARKER_RE.sub("", value)
    value = _PTY_OSC_RE.sub("", value)
    value = _PTY_ANSI_RE.sub("", value)
    value = value.replace("\r\n", "\n")
    value = "\n".join((line.split("\r")[-1] if line else "") for line in value.split("\n"))
    value = _PTY_CTRL_RE.sub("", value)
    return value


class PtyProcess:
    def __init__(
        self,
        session_id: str,
        command: str,
        pty_enabled: bool,
        buffer_size: Optional[int],
        idle_timeout_ms: int,
        writer: Optional[Callable[[bytes], int]],
        terminator: Callable[[], None],
        on_output: Optional[Callable[[bytes, int], None]] = None,
        pty_mode: str = "ephemeral",
    ):
        self.id: str = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.command = command
        self.pty_enabled = bool(pty_enabled)
        self.buffer_size = _clamp_buffer_size(buffer_size)
        self.idle_timeout_ms = int(idle_timeout_ms or 0)
        self._writer = writer
        self._terminator = terminator
        self._on_output = on_output
        self._on_state_change: Optional[Callable[[str], None]] = None
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._screen_lock = threading.Lock()
        self._completion_lock = threading.Lock()
        self._completion_keys: Set[str] = set()
        self._completion_reached: Set[str] = set()
        self._completion_tail = b""
        self._cursor = 0
        self._total_bytes = 0
        self._seq = 0
        self._last_output_at = time.monotonic()
        self._screen_text = ""
        self._screen_hash = hashlib.sha1(b"").hexdigest()
        self._screen_cols = DEFAULT_SCREEN_COLS
        self._screen_rows = DEFAULT_SCREEN_ROWS
        self._screen_fallback_max_chars = DEFAULT_SCREEN_FALLBACK_MAX_CHARS
        self._screen_fallback_max_lines = DEFAULT_SCREEN_FALLBACK_MAX_LINES
        self._screen_fallback_text = ""
        self._screen_backend = "fallback"
        self._pyte_screen = None
        self._pyte_stream = None
        self._init_screen_renderer()
        self.status = "running"
        self.exit_code: Optional[int] = None
        self.waiting_input: bool = False
        self.wait_reason: Optional[str] = None
        self.created_at = time.time()
        self.reader_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pty_mode = str(pty_mode or "ephemeral").strip().lower() or "ephemeral"
        self.pty_message_id: Optional[int] = None
        self.pty_live: bool = False
        self._pty_message_finalized: bool = False

    def _init_screen_renderer(self) -> None:
        cols = DEFAULT_SCREEN_COLS
        rows = DEFAULT_SCREEN_ROWS
        try:
            cols = int(os.environ.get("PTY_SCREEN_COLS") or cols)
        except (TypeError, ValueError):
            cols = DEFAULT_SCREEN_COLS
        try:
            rows = int(os.environ.get("PTY_SCREEN_ROWS") or rows)
        except (TypeError, ValueError):
            rows = DEFAULT_SCREEN_ROWS
        self._screen_cols = max(40, min(400, cols))
        self._screen_rows = max(10, min(200, rows))
        try:
            self._screen_fallback_max_chars = max(
                1000,
                int(os.environ.get("PTY_SCREEN_FALLBACK_MAX_CHARS") or DEFAULT_SCREEN_FALLBACK_MAX_CHARS)
            )
        except (TypeError, ValueError):
            self._screen_fallback_max_chars = DEFAULT_SCREEN_FALLBACK_MAX_CHARS
        try:
            self._screen_fallback_max_lines = max(
                20,
                int(os.environ.get("PTY_SCREEN_FALLBACK_MAX_LINES") or DEFAULT_SCREEN_FALLBACK_MAX_LINES)
            )
        except (TypeError, ValueError):
            self._screen_fallback_max_lines = DEFAULT_SCREEN_FALLBACK_MAX_LINES
        if pyte is None:
            return
        try:
            screen_cls = getattr(pyte, "HistoryScreen", None)
            if screen_cls is not None:
                self._pyte_screen = screen_cls(self._screen_cols, self._screen_rows, history=1000)
            else:
                self._pyte_screen = pyte.Screen(self._screen_cols, self._screen_rows)
            self._pyte_stream = pyte.Stream(self._pyte_screen)
            self._screen_backend = "pyte"
        except Exception:
            self._pyte_screen = None
            self._pyte_stream = None
            self._screen_backend = "fallback"

    @property
    def last_output_at(self) -> float:
        return self._last_output_at

    def touch_output(self) -> None:
        self._last_output_at = time.monotonic()

    def _touch_seq(self) -> None:
        self._seq += 1

    def _update_screen_hash(self) -> None:
        try:
            self._screen_hash = hashlib.sha1(self._screen_text.encode("utf-8", errors="replace")).hexdigest()
        except Exception:
            self._screen_hash = hashlib.sha1(b"").hexdigest()

    def _render_screen_text_locked(self) -> None:
        if self._screen_backend == "pyte" and self._pyte_screen is not None:
            try:
                display = list(getattr(self._pyte_screen, "display", []) or [])
                trimmed = [line.rstrip() for line in display]
                text = "\n".join(trimmed).rstrip("\n")
                self._screen_text = text
                self._update_screen_hash()
                return
            except Exception:
                self._screen_backend = "fallback"
        self._screen_text = self._screen_fallback_text
        self._update_screen_hash()

    def _append_screen_text_locked(self, data: bytes) -> None:
        if not data:
            return
        text = _decode_output_bytes(data)
        if self._screen_backend == "pyte" and self._pyte_stream is not None:
            try:
                self._pyte_stream.feed(text)
                self._render_screen_text_locked()
                return
            except Exception:
                self._screen_backend = "fallback"
        self._screen_fallback_text += text
        if len(self._screen_fallback_text) > self._screen_fallback_max_chars:
            self._screen_fallback_text = self._screen_fallback_text[-self._screen_fallback_max_chars:]
        if "\n" in self._screen_fallback_text:
            lines = self._screen_fallback_text.splitlines()
            if len(lines) > self._screen_fallback_max_lines:
                lines = lines[-self._screen_fallback_max_lines:]
                self._screen_fallback_text = "\n".join(lines)
        self._render_screen_text_locked()

    def register_completion_key(self, key: Optional[str]) -> str:
        token = str(key or "").strip()
        if not token:
            token = uuid.uuid4().hex
        with self._completion_lock:
            self._completion_keys.add(token)
        return token

    def is_completion_reached(self, key: Optional[str]) -> bool:
        token = str(key or "").strip()
        if not token:
            return self.status in ("exited", "closed")
        with self._completion_lock:
            if token in self._completion_reached:
                return True
        return self.status in ("exited", "closed")

    def _append_raw_output(self, data: bytes) -> None:
        if not data:
            return
        callback = self._on_output
        total_bytes = 0
        with self._buffer_lock:
            self._total_bytes += len(data)
            total_bytes = self._total_bytes
            self._buffer.extend(data)
            if len(self._buffer) > self.buffer_size:
                overflow = len(self._buffer) - self.buffer_size
                if overflow > 0:
                    del self._buffer[:overflow]
            self._last_output_at = time.monotonic()
        with self._screen_lock:
            self._append_screen_text_locked(data)
            self._touch_seq()
        if callback:
            try:
                callback(data, total_bytes)
            except Exception:
                pass

    def _consume_completion_markers(self, data: bytes, final: bool = False) -> bytes:
        with self._completion_lock:
            keys = [key for key in self._completion_keys if key]
            tail = self._completion_tail
            self._completion_tail = b""

        if not keys:
            if tail:
                return tail + (data or b"")
            return data or b""

        merged = (tail or b"") + (data or b"")
        if not merged:
            return b""

        text = merged.decode("latin-1", errors="ignore")
        lines = text.splitlines(keepends=True)
        tail_text = ""
        if not final and lines:
            last_line = lines[-1]
            if not last_line.endswith("\n") and not last_line.endswith("\r"):
                tail_text = last_line
                lines = lines[:-1]

        tracked = [(key, key.lower()) for key in keys]
        reached: Set[str] = set()
        kept_lines: List[str] = []

        for line in lines:
            lower_line = line.lower()
            hit = False
            for key, lower_key in tracked:
                if lower_key and lower_key in lower_line:
                    reached.add(key)
                    hit = True
            if not hit:
                kept_lines.append(line)

        if final and tail_text:
            lower_tail = tail_text.lower()
            hit = False
            for key, lower_key in tracked:
                if lower_key and lower_key in lower_tail:
                    reached.add(key)
                    hit = True
            if not hit:
                kept_lines.append(tail_text)
            tail_text = ""

        with self._completion_lock:
            if tail_text:
                self._completion_tail = tail_text.encode("latin-1", errors="ignore")
            if reached:
                self._completion_reached.update(reached)

        return "".join(kept_lines).encode("latin-1", errors="ignore")

    def _flush_completion_tail(self) -> None:
        flushed = self._consume_completion_markers(b"", final=True)
        if flushed:
            self._append_raw_output(flushed)

    def append_output(self, data: bytes) -> None:
        filtered = self._consume_completion_markers(data, final=False)
        if filtered:
            self._append_raw_output(filtered)

    def set_on_output(self, callback: Optional[Callable[[bytes, int], None]]) -> None:
        self._on_output = callback

    def set_on_state_change(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_state_change = callback

    def _emit_state_change(self) -> None:
        callback = self._on_state_change
        if callback:
            try:
                callback(self.status)
            except Exception:
                pass

    def update_waiting_state(self, waiting: bool, reason: Optional[str] = None) -> None:
        waiting_value = bool(waiting)
        reason_value = str(reason or "").strip() if waiting_value and reason else None
        changed = waiting_value != self.waiting_input or reason_value != self.wait_reason
        self.waiting_input = waiting_value
        self.wait_reason = reason_value
        if changed:
            with self._screen_lock:
                self._touch_seq()
            self._emit_state_change()

    def get_screen_text(self) -> str:
        with self._screen_lock:
            return self._screen_text

    def get_snapshot(self) -> Dict[str, Any]:
        with self._screen_lock:
            return {
                "seq": self._seq,
                "screen_text": self._screen_text,
                "screen_hash": self._screen_hash
            }

    def build_state_payload(self, cursor: Optional[int] = None) -> Dict[str, Any]:
        snap = self.get_snapshot()
        payload: Dict[str, Any] = {
            "pty_id": self.id,
            "status": self.status,
            "exit_code": self.exit_code,
            "waiting_input": self.waiting_input,
            "wait_reason": self.wait_reason,
            "seq": snap.get("seq"),
            "screen_text": snap.get("screen_text"),
            "screen_hash": snap.get("screen_hash"),
            "pty_mode": self.pty_mode,
            "pty_live": bool(self.pty_live)
        }
        if cursor is None:
            payload["cursor"] = self._cursor
        else:
            payload["cursor"] = cursor
        if self.pty_message_id is not None:
            payload["pty_message_id"] = self.pty_message_id
        return payload

    def read(self, cursor: Optional[int], max_output: int) -> Tuple[str, int, bool]:
        if max_output <= 0:
            return "", self._cursor, False
        acquired = self._buffer_lock.acquire(timeout=0.1)
        if not acquired:
            return "", self._cursor, False
        requested_cursor = self._cursor if cursor is None else int(cursor)
        buffer_start = 0
        reset = False
        log_total_bytes = 0
        log_buffer_len = 0
        try:
            buffer_start = self._total_bytes - len(self._buffer)
            effective_cursor = requested_cursor
            if effective_cursor < buffer_start:
                effective_cursor = buffer_start
                reset = True
            start_idx = effective_cursor - buffer_start
            end_idx = min(len(self._buffer), start_idx + max_output)
            chunk = bytes(self._buffer[start_idx:end_idx])
            new_cursor = effective_cursor + len(chunk)
            self._cursor = max(self._cursor, new_cursor)
            log_total_bytes = self._total_bytes
            log_buffer_len = len(self._buffer)
        finally:
            self._buffer_lock.release()
        if reset:
            print(
                "[PTY RESET] "
                f"session_id={self.session_id} "
                f"pty_id={self.id} "
                f"requested_cursor={requested_cursor} "
                f"buffer_start={buffer_start} "
                f"applied_cursor={self._cursor} "
                f"total_bytes={log_total_bytes} "
                f"buffer_len={log_buffer_len}"
            )
        return _decode_output_bytes(chunk), self._cursor, reset

    def write(self, data: bytes) -> int:
        if not data or not self._writer:
            return 0
        return self._writer(data)

    def mark_exited(self, exit_code: Optional[int]) -> None:
        if self.status == "closed":
            return
        self._flush_completion_tail()
        self.status = "exited"
        self.exit_code = exit_code
        with self._screen_lock:
            self._touch_seq()
        self._emit_state_change()

    def close(self) -> None:
        if self.status == "closed":
            return
        self._flush_completion_tail()
        self.status = "closed"
        self.stop_event.set()
        with self._screen_lock:
            self._touch_seq()
        self._emit_state_change()
        # Run terminator in a background thread to avoid blocking
        # the caller (e.g. asyncio event loop) if ClosePseudoConsole hangs
        t = threading.Thread(
            target=self._safe_terminate,
            daemon=True,
            name=f"pty-terminate-{self.id}"
        )
        t.start()

    def _safe_terminate(self) -> None:
        try:
            self._terminator()
        except Exception:
            pass


class PtyManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, PtyProcess]] = {}

    def register(self, proc: PtyProcess) -> str:
        with self._lock:
            session_map = self._sessions.setdefault(proc.session_id, {})
            session_map[proc.id] = proc
            return proc.id

    def get(self, session_id: str, pty_id: str) -> Optional[PtyProcess]:
        with self._lock:
            return self._sessions.get(session_id, {}).get(pty_id)

    def list(self, session_id: str) -> List[PtyProcess]:
        with self._lock:
            return list(self._sessions.get(session_id, {}).values())

    def close(self, session_id: str, pty_id: str, keep: bool = True) -> bool:
        proc = None
        with self._lock:
            session_map = self._sessions.get(session_id)
            if not session_map:
                return False
            proc = session_map.get(pty_id)
            if not proc:
                return False
            if not keep:
                session_map.pop(pty_id, None)
                if not session_map:
                    self._sessions.pop(session_id, None)
        if proc:
            proc.close()
            return True
        return False

    def close_session(self, session_id: str) -> int:
        with self._lock:
            session_map = self._sessions.pop(session_id, {})
        count = 0
        for proc in session_map.values():
            proc.close()
            count += 1
        return count

    def close_all(self) -> int:
        with self._lock:
            sessions = self._sessions
            self._sessions = {}
        count = 0
        for session_map in sessions.values():
            for proc in session_map.values():
                proc.close()
                count += 1
        return count


_PTY_MANAGER = PtyManager()


def get_pty_manager() -> PtyManager:
    return _PTY_MANAGER
