import threading
import time
import uuid
from typing import Dict, Optional, Tuple, List, Callable


DEFAULT_BUFFER_SIZE = 2 * 1024 * 1024
MAX_BUFFER_SIZE = 5 * 1024 * 1024


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
    try:
        import locale
        encoding = locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        encoding = "utf-8"
    return data.decode(encoding, errors="replace")


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
    ):
        self.id: str = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.command = command
        self.pty_enabled = bool(pty_enabled)
        self.buffer_size = _clamp_buffer_size(buffer_size)
        self.idle_timeout_ms = int(idle_timeout_ms or 0)
        self._writer = writer
        self._terminator = terminator
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._cursor = 0
        self._total_bytes = 0
        self._last_output_at = time.monotonic()
        self.status = "running"
        self.exit_code: Optional[int] = None
        self.created_at = time.time()
        self.reader_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    @property
    def last_output_at(self) -> float:
        return self._last_output_at

    def touch_output(self) -> None:
        self._last_output_at = time.monotonic()

    def append_output(self, data: bytes) -> None:
        if not data:
            return
        with self._buffer_lock:
            self._total_bytes += len(data)
            self._buffer.extend(data)
            if len(self._buffer) > self.buffer_size:
                overflow = len(self._buffer) - self.buffer_size
                if overflow > 0:
                    del self._buffer[:overflow]
            self._last_output_at = time.monotonic()

    def read(self, cursor: Optional[int], max_output: int) -> Tuple[str, int, bool]:
        if max_output <= 0:
            return "", self._cursor, False
        acquired = self._buffer_lock.acquire(timeout=0.1)
        if not acquired:
            return "", self._cursor, False
        try:
            buffer_start = self._total_bytes - len(self._buffer)
            effective_cursor = self._cursor if cursor is None else int(cursor)
            reset = False
            if effective_cursor < buffer_start:
                effective_cursor = buffer_start
                reset = True
            start_idx = effective_cursor - buffer_start
            end_idx = min(len(self._buffer), start_idx + max_output)
            chunk = bytes(self._buffer[start_idx:end_idx])
            new_cursor = effective_cursor + len(chunk)
            self._cursor = max(self._cursor, new_cursor)
        finally:
            self._buffer_lock.release()
        return _decode_output_bytes(chunk), self._cursor, reset

    def write(self, data: bytes) -> int:
        if not data or not self._writer:
            return 0
        return self._writer(data)

    def mark_exited(self, exit_code: Optional[int]) -> None:
        if self.status == "closed":
            return
        self.status = "exited"
        self.exit_code = exit_code

    def close(self) -> None:
        if self.status == "closed":
            return
        self.status = "closed"
        self.stop_event.set()
        # Run termination in a background thread to avoid blocking the
        # asyncio event loop.  The _safe_terminate helper first waits for
        # the reader thread to exit (so no thread is still touching the
        # pipe handles), then invokes the platform terminator with a
        # timeout guard against ClosePseudoConsole hangs.
        threading.Thread(
            target=self._safe_terminate,
            name=f"pty-close-{self.id}",
            daemon=True
        ).start()

    def _safe_terminate(self) -> None:
        # Step 1: Wait for the reader thread to notice stop_event and exit
        # cleanly.  This ensures no thread is still calling ReadFile /
        # PeekNamedPipe on the output pipe when we close handles.
        if self.reader_thread is not None and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=5.0)

        # Step 2: Run the actual terminator (TerminateProcess, CloseHandle,
        # ClosePseudoConsole, etc.) inside yet another thread with a timeout
        # so that even if ClosePseudoConsole blocks indefinitely we do not
        # leak a permanently-stuck thread.
        term_thread = threading.Thread(
            target=self._run_terminator,
            name=f"pty-term-{self.id}",
            daemon=True
        )
        term_thread.start()
        term_thread.join(timeout=10.0)
        if term_thread.is_alive():
            print(
                f"[PTY WARN] terminator timed out for pty_id={self.id}, "
                f"handle may be leaked"
            )

    def _run_terminator(self) -> None:
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
