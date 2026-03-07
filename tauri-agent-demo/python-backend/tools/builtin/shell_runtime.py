import asyncio
import json
import locale
import os
import re
import select
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app_config import get_app_config
from pty_stream_registry import get_pty_stream_registry
from repositories import chat_repository

from ..base import Tool, ToolParameter
from ..config import get_tool_config, update_tool_config
from ..context import get_tool_context
from ..pty_manager import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_SCREEN_COLS,
    DEFAULT_SCREEN_ROWS,
    PtyProcess,
    _decode_output_bytes,
    get_pty_manager,
    sanitize_pty_screen_text,
)
from .common import (
    _extract_command_name,
    _get_agent_mode,
    _get_allowed_roots,
    _get_root_path,
    _get_session_id,
    _parse_json_input,
    _rewrite_rg_command,
)

try:
    import pty as posix_pty
except Exception:
    posix_pty = None

_ANSI_ESCAPE_RE = re.compile(r"[\u001b\u009b][\\[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[@-~]")
_INTERACTIVE_PROMPT_PATTERNS = [
    re.compile(r"use\s+arrow\s+keys", re.IGNORECASE),
    re.compile(r"\bselect\b", re.IGNORECASE),
    re.compile(r"\bchoose\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\(y\/n\)|\(y\/N\)|\[y\/n\]|\[Y\/n\]", re.IGNORECASE),
    re.compile("\u8bf7\u9009\u62e9"),
    re.compile("\u8bf7\u8f93\u5165"),
]

def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _pty_debug_enabled() -> bool:
    return False


def _pty_debug(message: str) -> None:
    return


def _tail_bytes_hex(data: bytes, max_len: int = 16) -> str:
    if not data:
        return ""
    return data[-max_len:].hex()


def _use_restricted_conpty(use_sandbox: bool) -> bool:
    return bool(use_sandbox)


def _emit_pty_message_upsert(
    session_id: str,
    pty_proc: PtyProcess,
    *,
    final: bool = False,
    content_override: Optional[str] = None
) -> None:
    if not session_id or not getattr(pty_proc, "pty_message_id", None):
        return
    stream_registry = get_pty_stream_registry()
    content = content_override if content_override is not None else pty_proc.get_screen_text()
    content = sanitize_pty_screen_text(content)
    payload = {
        "event": "pty_message_upsert",
        "session_id": session_id,
        "pty_id": pty_proc.id,
        "message_id": int(pty_proc.pty_message_id),
        "content": str(content or ""),
        "status": str(pty_proc.status or ""),
        "final": bool(final)
    }
    stream_registry.emit_threadsafe(session_id, payload)


def _create_persistent_pty_message(pty_proc: PtyProcess) -> Optional[int]:
    if not pty_proc or getattr(pty_proc, "pty_message_id", None):
        return getattr(pty_proc, "pty_message_id", None)
    session_id = getattr(pty_proc, "session_id", "")
    if not session_id:
        return None
    try:
        from models import ChatMessageCreate
        from repositories import chat_repository
        message = chat_repository.create_message(ChatMessageCreate(
            session_id=session_id,
            role="assistant",
            content="",
            metadata={
                "pty_live": True,
                "persistent": True,
                "pty_id": pty_proc.id
            }
        ))
        message_id = int(message.id or 0) if message and getattr(message, "id", None) is not None else 0
        if message_id <= 0:
            return None
        pty_proc.pty_message_id = message_id
        pty_proc.pty_live = True
        pty_proc._pty_message_finalized = False
        _emit_pty_message_upsert(session_id, pty_proc, final=False, content_override="")
        return message_id
    except Exception as exc:
        _pty_debug(f"pty_message create failed pty_id={getattr(pty_proc, 'id', '')} error={exc}")
        return None


def _persist_persistent_pty_message_final(pty_proc: PtyProcess) -> None:
    if not pty_proc:
        return
    message_id = getattr(pty_proc, "pty_message_id", None)
    if not message_id:
        return
    if getattr(pty_proc, "_pty_message_finalized", False):
        return
    session_id = getattr(pty_proc, "session_id", "")
    if not session_id:
        return
    final_content = sanitize_pty_screen_text(pty_proc.get_screen_text() or "")
    try:
        record = chat_repository.get_message_details(session_id, int(message_id))
        metadata = record.get("metadata") if isinstance(record, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update({
            "pty_live": False,
            "persistent": True,
            "pty_id": pty_proc.id,
            "final": True,
            "status": str(pty_proc.status or "")
        })
        chat_repository.update_message_content_and_metadata(
            session_id,
            int(message_id),
            final_content,
            metadata,
        )
        pty_proc._pty_message_finalized = True
        pty_proc.pty_live = False
    except Exception as exc:
        _pty_debug(f"pty_message finalize failed pty_id={pty_proc.id} error={exc}")
    _emit_pty_message_upsert(session_id, pty_proc, final=True, content_override=final_content)


def _attach_pty_sse_emitter(pty_proc: PtyProcess) -> None:
    session_id = getattr(pty_proc, "session_id", "")
    if not session_id:
        return
    stream_registry = get_pty_stream_registry()

    def _emit(chunk: bytes, cursor: int) -> None:
        text = _decode_output_bytes(chunk)
        state_payload = pty_proc.build_state_payload(cursor=cursor)
        state_payload.pop("screen_text", None)
        payload = {
            "event": "pty_delta",
            "session_id": session_id,
            "chunk": text,
            **state_payload
        }
        stream_registry.emit_threadsafe(session_id, payload)
        if getattr(pty_proc, "pty_message_id", None):
            _emit_pty_message_upsert(session_id, pty_proc, final=False)

    def _emit_state(_status: str) -> None:
        state_payload = pty_proc.build_state_payload()
        state_payload.pop("screen_text", None)
        payload = {
            "event": "pty_state",
            "session_id": session_id,
            "chunk": "",
            **state_payload
        }
        stream_registry.emit_threadsafe(session_id, payload)
        if getattr(pty_proc, "pty_message_id", None):
            if pty_proc.status in ("exited", "closed"):
                _persist_persistent_pty_message_final(pty_proc)
            else:
                _emit_pty_message_upsert(session_id, pty_proc, final=False)

    pty_proc.set_on_output(_emit)
    pty_proc.set_on_state_change(_emit_state)


def _safe_close_pseudo_console(kernel32_dll, h_pc_handle, label: str = "conpty") -> None:
    """Close PseudoConsole with timeout protection to prevent deadlock.

    ClosePseudoConsole is a synchronous blocking call that waits for
    conhost.exe to finish cleanup. If child processes are still alive,
    it can block indefinitely, hanging the calling thread.
    """
    skip_raw = os.environ.get("PTY_SKIP_CLOSE_PSEUDOCONSOLE")
    if skip_raw is not None and str(skip_raw).strip().lower() in ("1", "true", "yes", "on"):
        _pty_debug(f"{label} ClosePseudoConsole skipped")
        return

    def _do_close():
        try:
            kernel32_dll.ClosePseudoConsole(h_pc_handle)
        except Exception:
            pass

    _pty_debug(f"{label} ClosePseudoConsole enter")
    t = threading.Thread(target=_do_close, daemon=True)
    t.start()

    timeout = 5.0
    try:
        raw = os.environ.get("PTY_CLOSE_PSEUDOCONSOLE_TIMEOUT_SEC")
        if raw is not None:
            timeout = float(raw)
    except (TypeError, ValueError):
        pass
    if timeout <= 0:
        timeout = 5.0

    t.join(timeout=timeout)
    if t.is_alive():
        _pty_debug(f"{label} ClosePseudoConsole timed out after {timeout}s, abandoned")
    else:
        _pty_debug(f"{label} ClosePseudoConsole exit")


_PTY_SUPPORT: Optional[bool] = None


def _windows_wrap_command(command: str, keep_open: bool) -> str:
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    flag = "/k" if keep_open else "/c"
    if not str(command).strip():
        return f'"{comspec}" {flag}'
    return f'"{comspec}" {flag} {command}'


def _supports_pty() -> bool:
    global _PTY_SUPPORT
    if _PTY_SUPPORT is not None:
        return _PTY_SUPPORT
    if _is_windows():
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            build = getattr(sys.getwindowsversion(), "build", 0)
            _PTY_SUPPORT = bool(getattr(kernel32, "CreatePseudoConsole", None)) and build >= 17763
            return _PTY_SUPPORT
        except Exception:
            _PTY_SUPPORT = False
            return _PTY_SUPPORT
    _PTY_SUPPORT = posix_pty is not None
    return _PTY_SUPPORT


def _escape_seatbelt_path(path: Path) -> str:
    raw = str(path)
    return raw.replace("\\", "\\\\").replace('"', '\\"')


def _build_macos_sandbox_profile(roots: List[Path]) -> str:
    allowed_paths = [_escape_seatbelt_path(root) for root in roots if root]
    profile_lines = [
        "(version 1)",
        "(allow default)",
        '(deny file-write* (subpath "/"))'
    ]
    allow_blocks = [
        '(allow file-write* (subpath "/tmp") (subpath "/private/tmp") (subpath "/var/tmp") (subpath "/dev"))'
    ]
    if allowed_paths:
        roots_clause = " ".join([f'(subpath "{path}")' for path in allowed_paths])
        allow_blocks.append(f"(allow file-write* {roots_clause})")
    profile_lines.extend(allow_blocks)
    return "\n".join(profile_lines)


def _run_macos_sandboxed(command: str, workdir: Path, timeout_sec: float) -> Tuple[int, str]:
    sandbox_exec = "/usr/bin/sandbox-exec"
    if not Path(sandbox_exec).exists():
        raise RuntimeError("sandbox-exec not available on this system.")
    roots = _get_allowed_roots()
    profile = _build_macos_sandbox_profile(roots)
    print(f"[Shell Sandbox] macos sandbox-exec={sandbox_exec} shell=/bin/sh workdir={workdir}")
    proc = subprocess.run(
        [sandbox_exec, "-p", profile, "/bin/sh", "-c", command],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout_sec
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def _run_windows_restricted(command: str, workdir: Path, timeout_sec: float) -> Tuple[int, str]:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    print(f"[Shell Sandbox] windows comspec={comspec} workdir={workdir}")

    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_ADJUST_DEFAULT = 0x0080
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    DISABLE_MAX_PRIVILEGE = 0x00000001

    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    STARTF_USESTDHANDLES = 0x00000100
    HANDLE_FLAG_INHERIT = 0x00000001
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    SE_GROUP_INTEGRITY = 0x00000020
    SE_PRIVILEGE_ENABLED = 0x00000002
    ERROR_NOT_ALL_ASSIGNED = 1300
    TokenIntegrityLevel = 25
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL)
        ]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE)
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD)
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD)
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong)
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t)
        ]

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES)]

    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFO),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

    def _raise_last_error(message: str) -> None:
        err = ctypes.get_last_error()
        print(f"[Shell Sandbox] {message} (winerr={err})")
        raise RuntimeError(f"{message} (winerr={err})")

    h_process = None
    h_process_opened = False
    try:
        pid = kernel32.GetCurrentProcessId()
        h_process = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid
        )
        if h_process:
            h_process_opened = True
            print(f"[Shell Sandbox] OpenProcess handle acquired pid={pid}")
    except Exception:
        h_process = None

    if not h_process:
        h_process = kernel32.GetCurrentProcess()
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        h_process,
        TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(h_token)
    ):
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        _raise_last_error("OpenProcessToken failed")

    def _enable_privilege(token_handle: wintypes.HANDLE, name: str) -> None:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            print(f"[Shell Sandbox] LookupPrivilegeValue failed for {name} (winerr={ctypes.get_last_error()})")
            return
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges = LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)
        if not advapi32.AdjustTokenPrivileges(token_handle, False, ctypes.byref(tp), 0, None, None):
            print(f"[Shell Sandbox] AdjustTokenPrivileges failed for {name} (winerr={ctypes.get_last_error()})")
            return
        last_err = ctypes.get_last_error()
        if last_err == ERROR_NOT_ALL_ASSIGNED:
            print(f"[Shell Sandbox] Privilege not assigned: {name}")

    _enable_privilege(h_token, "SeChangeNotifyPrivilege")

    restricted_token = wintypes.HANDLE()
    if not advapi32.CreateRestrictedToken(
        h_token,
        DISABLE_MAX_PRIVILEGE,
        0,
        None,
        0,
        None,
        0,
        None,
        ctypes.byref(restricted_token)
    ):
        kernel32.CloseHandle(h_token)
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        _raise_last_error("CreateRestrictedToken failed")

    # Do not lower integrity by default; leave token at the caller's integrity level.

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = True

    stdout_read = wintypes.HANDLE()
    stdout_write = wintypes.HANDLE()
    if not kernel32.CreatePipe(ctypes.byref(stdout_read), ctypes.byref(stdout_write), ctypes.byref(sa), 0):
        kernel32.CloseHandle(restricted_token)
        kernel32.CloseHandle(h_token)
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        _raise_last_error("CreatePipe failed")
    if not kernel32.SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0):
        kernel32.CloseHandle(stdout_read)
        kernel32.CloseHandle(stdout_write)
        kernel32.CloseHandle(restricted_token)
        kernel32.CloseHandle(h_token)
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        _raise_last_error("SetHandleInformation failed")

    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(STARTUPINFO)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdOutput = stdout_write
    startup.hStdError = stdout_write
    startup.hStdInput = kernel32.GetStdHandle(-10)

    proc_info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(f'"{comspec}" /c {command}')

    created = advapi32.CreateProcessAsUserW(
        restricted_token,
        None,
        command_line,
        None,
        None,
        True,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        None,
        str(workdir),
        ctypes.byref(startup),
        ctypes.byref(proc_info)
    )

    kernel32.CloseHandle(stdout_write)
    kernel32.CloseHandle(restricted_token)
    kernel32.CloseHandle(h_token)
    if h_process_opened:
        kernel32.CloseHandle(h_process)

    if not created:
        kernel32.CloseHandle(stdout_read)
        _raise_last_error("CreateProcessAsUserW failed")

    job_handle = kernel32.CreateJobObjectW(None, None)
    if job_handle:
        job_info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        job_info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(job_info),
            ctypes.sizeof(job_info)
        )
        if not kernel32.AssignProcessToJobObject(job_handle, proc_info.hProcess):
            err = ctypes.get_last_error()
            print(f"[Shell Sandbox] AssignProcessToJobObject failed (winerr={err})")

    output_chunks: List[bytes] = []
    start_time = time.monotonic()
    buffer = ctypes.create_string_buffer(4096)
    bytes_read = wintypes.DWORD()

    while True:
        if timeout_sec is not None and timeout_sec > 0:
            remaining = max(0.0, timeout_sec - (time.monotonic() - start_time))
            wait_ms = int(min(50.0, remaining) * 1000)
            if remaining <= 0:
                timeout_output = _decode_output_bytes(b"".join(output_chunks))
                kernel32.TerminateProcess(proc_info.hProcess, 1)
                kernel32.CloseHandle(proc_info.hThread)
                kernel32.CloseHandle(proc_info.hProcess)
                kernel32.CloseHandle(stdout_read)
                if job_handle:
                    kernel32.CloseHandle(job_handle)
                raise subprocess.TimeoutExpired(command, timeout_sec, output=timeout_output)
        else:
            wait_ms = 50

        wait_result = kernel32.WaitForSingleObject(proc_info.hProcess, wait_ms)

        while True:
            available = wintypes.DWORD()
            if not kernel32.PeekNamedPipe(stdout_read, None, 0, None, ctypes.byref(available), None):
                break
            if available.value == 0:
                break
            to_read = min(len(buffer), available.value)
            if kernel32.ReadFile(stdout_read, buffer, to_read, ctypes.byref(bytes_read), None):
                if bytes_read.value > 0:
                    output_chunks.append(buffer.raw[:bytes_read.value])

        if wait_result == WAIT_OBJECT_0:
            break
        if wait_result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            break

    while True:
        available = wintypes.DWORD()
        if not kernel32.PeekNamedPipe(stdout_read, None, 0, None, ctypes.byref(available), None):
            break
        if available.value == 0:
            break
        to_read = min(len(buffer), available.value)
        if kernel32.ReadFile(stdout_read, buffer, to_read, ctypes.byref(bytes_read), None):
            if bytes_read.value > 0:
                output_chunks.append(buffer.raw[:bytes_read.value])

    exit_code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
    kernel32.CloseHandle(proc_info.hThread)
    kernel32.CloseHandle(proc_info.hProcess)
    kernel32.CloseHandle(stdout_read)
    if job_handle:
        kernel32.CloseHandle(job_handle)

    raw_output = b"".join(output_chunks)
    output = _decode_output_bytes(raw_output)
    return int(exit_code.value), output


def _run_sandboxed_command(command: str, workdir: Path, timeout_sec: float) -> Tuple[int, str]:
    if _is_windows():
        try:
            return _run_windows_restricted(command, workdir, timeout_sec)
        except Exception as exc:
            print(f"[Shell Sandbox] windows restricted failed: {exc}")
            raise
    if _is_macos():
        try:
            return _run_macos_sandboxed(command, workdir, timeout_sec)
        except Exception as exc:
            print(f"[Shell Sandbox] macos sandbox-exec failed: {exc}")
            raise
    print("[Shell Sandbox] unsupported platform, falling back to shell execution")
    proc = subprocess.run(
        command,
        cwd=str(workdir),
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_sec
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def _apply_max_output(output: str, max_output: int) -> str:
    if max_output > 0 and len(output) > max_output:
        return output[:max_output] + "\n... (truncated)"
    return output


def _resolve_idle_timeout_ms(mode: str, idle_timeout_ms: Optional[int]) -> int:
    if idle_timeout_ms is not None:
        try:
            return int(idle_timeout_ms)
        except (TypeError, ValueError):
            return 0
    return 0 if mode == "persistent" else 120000


def _resolve_buffer_size(raw_size: Optional[int]) -> int:
    try:
        config_default = int(get_tool_config().get("shell", {}).get("buffer_size", DEFAULT_BUFFER_SIZE))
    except (TypeError, ValueError):
        config_default = DEFAULT_BUFFER_SIZE
    if raw_size is None:
        return config_default
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        return config_default
    if size <= 0:
        return config_default
    return size


def _encode_stdin(stdin_text: Optional[str]) -> bytes:
    if not stdin_text:
        return b""
    if isinstance(stdin_text, bytes):
        return stdin_text
    return str(stdin_text).encode("utf-8", errors="replace")


def _normalize_windows_stdin(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.replace("\n", "\r\n")


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _strip_ansi(text: str) -> str:
    if not text:
        return ""
    output = _ANSI_ESCAPE_RE.sub("", text)
    output = output.replace("\x07", "")
    return output


def _detect_wait_reason_from_output(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = _strip_ansi(str(text)).replace("\r\n", "\n").replace("\r", "\n")
    trimmed = normalized.strip()
    if not trimmed:
        return None

    for pattern in _INTERACTIVE_PROMPT_PATTERNS:
        if pattern.search(trimmed):
            return "prompt_detected"

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if lines:
        tail = lines[-1]
        if len(tail) <= 160 and re.search(r"[:?]\s*$", tail):
            return "stdin_needed"
    return None


def _set_waiting_state(proc: PtyProcess, waiting: bool, reason: Optional[str] = None) -> None:
    if hasattr(proc, "update_waiting_state"):
        proc.update_waiting_state(waiting, reason)
        return
    proc.waiting_input = bool(waiting)
    proc.wait_reason = str(reason or "").strip() if waiting and reason else None


def _update_waiting_state_from_chunk(proc: PtyProcess, chunk: str) -> Tuple[bool, Optional[str]]:
    if proc.status != "running":
        _set_waiting_state(proc, False, None)
        return proc.waiting_input, proc.wait_reason

    text = str(chunk or "")
    if text.strip():
        reason = _detect_wait_reason_from_output(text)
        if reason:
            _set_waiting_state(proc, True, reason)
        else:
            _set_waiting_state(proc, False, None)
    return proc.waiting_input, proc.wait_reason


def _format_pty_header(
    proc: PtyProcess,
    *,
    cursor: Optional[int] = None,
    reset: Optional[bool] = None,
    bytes_written: Optional[int] = None
) -> str:
    state_payload: Dict[str, Any]
    if hasattr(proc, "build_state_payload"):
        state_payload = proc.build_state_payload(cursor=cursor)
    else:
        state_payload = {
            "pty_id": proc.id,
            "status": proc.status,
            "cursor": cursor,
            "waiting_input": bool(getattr(proc, "waiting_input", False)),
            "wait_reason": getattr(proc, "wait_reason", None),
            "pty_mode": getattr(proc, "pty_mode", "ephemeral"),
            "pty_live": bool(getattr(proc, "pty_live", False))
        }
    tokens = [
        f"pty_id={state_payload.get('pty_id') or proc.id}",
        f"status={state_payload.get('status') or proc.status}",
        f"pty={str(proc.pty_enabled).lower()}",
        f"cursor={state_payload.get('cursor')}",
        f"idle_timeout={proc.idle_timeout_ms}",
        f"buffer_size={proc.buffer_size}",
        f"pty_mode={state_payload.get('pty_mode') or getattr(proc, 'pty_mode', 'ephemeral')}",
        f"seq={state_payload.get('seq') if state_payload.get('seq') is not None else 0}",
        f"pty_live={str(bool(state_payload.get('pty_live'))).lower()}",
    ]
    if state_payload.get("screen_hash"):
        tokens.append(f"screen_hash={state_payload.get('screen_hash')}")
    if state_payload.get("waiting_input") is not None:
        tokens.append(f"waiting_input={str(bool(state_payload.get('waiting_input'))).lower()}")
    if state_payload.get("wait_reason"):
        tokens.append(f"wait_reason={state_payload.get('wait_reason')}")
    if reset is not None:
        tokens.append(f"reset={str(bool(reset)).lower()}")
    if proc.status in ("exited", "closed"):
        tokens.append(f"exit_code={proc.exit_code}")
    if bytes_written is not None:
        tokens.append(f"bytes_written={int(bytes_written)}")
    if getattr(proc, "pty_message_id", None) is not None:
        tokens.append(f"pty_message_id={int(proc.pty_message_id)}")
    return f"[{' '.join(tokens)}]"


def _build_posix_command(command: str, use_sandbox: bool = True) -> List[str]:
    if _is_macos() and use_sandbox:
        sandbox_exec = "/usr/bin/sandbox-exec"
        if not Path(sandbox_exec).exists():
            raise RuntimeError("sandbox-exec not available on this system.")
        roots = _get_allowed_roots()
        profile = _build_macos_sandbox_profile(roots)
        return [sandbox_exec, "-p", profile, "/bin/sh", "-c", command]
    return ["/bin/sh", "-c", command]


def _terminate_posix_process(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, 15)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _run_posix_pty_oneshot(
    command: str,
    workdir: Path,
    timeout_sec: float,
    idle_timeout_ms: int,
    stdin_bytes: bytes,
    use_sandbox: bool
) -> Tuple[int, str]:
    if posix_pty is None:
        raise RuntimeError("PTY not available on this platform.")
    master_fd, slave_fd = posix_pty.openpty()
    cmd_list = _build_posix_command(command, use_sandbox=use_sandbox)
    proc = subprocess.Popen(
        cmd_list,
        cwd=str(workdir),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid
    )
    os.close(slave_fd)
    try:
        os.set_blocking(master_fd, False)
    except Exception:
        pass
    if stdin_bytes:
        try:
            os.write(master_fd, stdin_bytes)
        except Exception:
            pass
    output_chunks: List[bytes] = []
    start_time = time.monotonic()
    last_output = start_time
    timed_out = False
    exit_code: Optional[int] = None
    timed_out = False
    while True:
        if timeout_sec is not None and timeout_sec > 0:
            if (time.monotonic() - start_time) >= timeout_sec:
                _terminate_posix_process(proc)
                exit_code = proc.poll()
                timed_out = True
                break
        if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
            _terminate_posix_process(proc)
            exit_code = proc.poll()
            output_chunks.append(b"\n[idle_timeout]\n")
            break
        rlist, _, _ = select.select([master_fd], [], [], 0.1)
        if rlist:
            try:
                data = os.read(master_fd, 4096)
            except Exception:
                data = b""
            if data:
                output_chunks.append(data)
                last_output = time.monotonic()
            else:
                if proc.poll() is not None:
                    break
        if proc.poll() is not None and not rlist:
            break
    try:
        exit_code = proc.poll()
    except Exception:
        pass
    try:
        os.close(master_fd)
    except Exception:
        pass
    if timed_out:
        timeout_output = _decode_output_bytes(b"".join(output_chunks))
        raise subprocess.TimeoutExpired(command, timeout_sec, output=timeout_output)
    return int(exit_code or 0), _decode_output_bytes(b"".join(output_chunks))


def _start_posix_pty_persistent(
    command: str,
    workdir: Path,
    stdin_bytes: bytes,
    idle_timeout_ms: int,
    buffer_size: int,
    use_sandbox: bool
) -> PtyProcess:
    if posix_pty is None:
        raise RuntimeError("PTY not available on this platform.")
    master_fd, slave_fd = posix_pty.openpty()
    cmd_list = _build_posix_command(command, use_sandbox=use_sandbox)
    proc = subprocess.Popen(
        cmd_list,
        cwd=str(workdir),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid
    )
    os.close(slave_fd)
    try:
        os.set_blocking(master_fd, False)
    except Exception:
        pass

    def _writer(data: bytes) -> int:
        try:
            return os.write(master_fd, data)
        except Exception:
            return 0

    def _terminator() -> None:
        if _pty_debug_enabled():
            _pty_debug(f"posix pty terminator enter pid={proc.pid}")
        _terminate_posix_process(proc)
        try:
            os.close(master_fd)
        except Exception:
            pass
        if _pty_debug_enabled():
            _pty_debug(f"posix pty terminator exit pid={proc.pid}")

    pty_proc = PtyProcess(
        session_id=_get_session_id(),
        command=command,
        pty_enabled=True,
        buffer_size=buffer_size,
        idle_timeout_ms=idle_timeout_ms,
        writer=_writer,
        terminator=_terminator,
        pty_mode="persistent"
    )
    _attach_pty_sse_emitter(pty_proc)

    def _reader_loop() -> None:
        last_output = time.monotonic()
        while not pty_proc.stop_event.is_set():
            rlist, _, _ = select.select([master_fd], [], [], 0.1)
            if rlist:
                try:
                    data = os.read(master_fd, 4096)
                except Exception:
                    data = b""
                if data:
                    pty_proc.append_output(data)
                    last_output = time.monotonic()
                else:
                    if proc.poll() is not None:
                        break
            if proc.poll() is not None and not rlist:
                break
            if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
                pty_proc.append_output(b"\n[idle_timeout]\n")
                _terminate_posix_process(proc)
                break
        exit_code = proc.poll()
        pty_proc.mark_exited(exit_code)
        try:
            os.close(master_fd)
        except Exception:
            pass

    thread = threading.Thread(target=_reader_loop, name=f"pty-reader-{pty_proc.id}", daemon=True)
    pty_proc.reader_thread = thread
    thread.start()
    if stdin_bytes:
        _writer(stdin_bytes)
    return pty_proc


def _start_posix_pipe_persistent(
    command: str,
    workdir: Path,
    stdin_bytes: bytes,
    idle_timeout_ms: int,
    buffer_size: int,
    use_sandbox: bool
) -> PtyProcess:
    cmd_list = _build_posix_command(command, use_sandbox=use_sandbox)
    proc = subprocess.Popen(
        cmd_list,
        cwd=str(workdir),
        shell=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid
    )

    def _writer(data: bytes) -> int:
        if not proc.stdin:
            return 0
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
            return len(data)
        except Exception:
            return 0

    def _terminator() -> None:
        if _pty_debug_enabled():
            _pty_debug(f"posix pipe terminator enter pid={proc.pid}")
        _terminate_posix_process(proc)
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if _pty_debug_enabled():
            _pty_debug(f"posix pipe terminator exit pid={proc.pid}")

    pty_proc = PtyProcess(
        session_id=_get_session_id(),
        command=command,
        pty_enabled=False,
        buffer_size=buffer_size,
        idle_timeout_ms=idle_timeout_ms,
        writer=_writer,
        terminator=_terminator,
        pty_mode="persistent"
    )
    _attach_pty_sse_emitter(pty_proc)

    def _reader_loop() -> None:
        last_output = time.monotonic()
        stdout = proc.stdout
        if stdout is None:
            pty_proc.mark_exited(proc.poll())
            return
        fd = stdout.fileno()
        try:
            os.set_blocking(fd, False)
        except Exception:
            pass
        while not pty_proc.stop_event.is_set():
            rlist, _, _ = select.select([fd], [], [], 0.1)
            if rlist:
                try:
                    data = os.read(fd, 4096)
                except Exception:
                    data = b""
                if data:
                    pty_proc.append_output(data)
                    last_output = time.monotonic()
                else:
                    if proc.poll() is not None:
                        break
            if proc.poll() is not None and not rlist:
                break
            if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
                pty_proc.append_output(b"\n[idle_timeout]\n")
                _terminate_posix_process(proc)
                break
        pty_proc.mark_exited(proc.poll())
        try:
            stdout.close()
        except Exception:
            pass

    thread = threading.Thread(target=_reader_loop, name=f"pipe-reader-{pty_proc.id}", daemon=True)
    pty_proc.reader_thread = thread
    thread.start()
    if stdin_bytes:
        _writer(stdin_bytes)
    return pty_proc


def _windows_create_restricted_token(low_integrity: bool = False):
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_ADJUST_DEFAULT = 0x0080
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    DISABLE_MAX_PRIVILEGE = 0x00000001

    SE_GROUP_INTEGRITY = 0x00000020
    SE_PRIVILEGE_ENABLED = 0x00000002
    ERROR_NOT_ALL_ASSIGNED = 1300
    TokenIntegrityLevel = 25

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES)]

    h_process = None
    h_process_opened = False
    try:
        pid = kernel32.GetCurrentProcessId()
        h_process = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid
        )
        if h_process:
            h_process_opened = True
    except Exception:
        h_process = None

    if not h_process:
        h_process = kernel32.GetCurrentProcess()
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        h_process,
        TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(h_token)
    ):
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        raise RuntimeError("OpenProcessToken failed")

    def _enable_privilege(token_handle: wintypes.HANDLE, name: str) -> None:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges = LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)
        if not advapi32.AdjustTokenPrivileges(token_handle, False, ctypes.byref(tp), 0, None, None):
            return
        last_err = ctypes.get_last_error()
        if last_err == ERROR_NOT_ALL_ASSIGNED:
            return

    _enable_privilege(h_token, "SeChangeNotifyPrivilege")

    restricted_token = wintypes.HANDLE()
    if not advapi32.CreateRestrictedToken(
        h_token,
        DISABLE_MAX_PRIVILEGE,
        0,
        None,
        0,
        None,
        0,
        None,
        ctypes.byref(restricted_token)
    ):
        kernel32.CloseHandle(h_token)
        if h_process_opened:
            kernel32.CloseHandle(h_process)
        raise RuntimeError("CreateRestrictedToken failed")

    if low_integrity:
        sid = wintypes.LPVOID()
        if advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):
            tml = TOKEN_MANDATORY_LABEL()
            tml.Label.Sid = sid
            tml.Label.Attributes = SE_GROUP_INTEGRITY
            advapi32.SetTokenInformation(
                restricted_token,
                TokenIntegrityLevel,
                ctypes.byref(tml),
                ctypes.sizeof(tml)
            )
            kernel32.LocalFree(sid)

    kernel32.CloseHandle(h_token)
    if h_process_opened:
        kernel32.CloseHandle(h_process)
    return restricted_token, kernel32, advapi32


def _windows_assign_job(kernel32, process_handle):
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD)
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong)
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t)
        ]

    job_handle = kernel32.CreateJobObjectW(None, None)
    if job_handle:
        job_info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        job_info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(job_info),
            ctypes.sizeof(job_info)
        )
        kernel32.AssignProcessToJobObject(job_handle, process_handle)
    return job_handle


def _windows_start_conpty_process(
    command: str,
    workdir: Path,
    cols: int = DEFAULT_SCREEN_COLS,
    rows: int = DEFAULT_SCREEN_ROWS,
    keep_open: bool = False,
    use_restricted_token: bool = True
):
    import ctypes
    from ctypes import wintypes

    restricted_token = None
    advapi32 = None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if use_restricted_token:
        # ConPTY needs a restricted token, but low-integrity can block console handle wiring.
        restricted_token, kernel32, advapi32 = _windows_create_restricted_token(low_integrity=False)
        _pty_debug("conpty restricted_token_low_integrity=false")
    _pty_debug(f"conpty restricted_token={str(bool(use_restricted_token)).lower()}")

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL)
        ]

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE)
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", wintypes.LPVOID)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD)
        ]

    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    HANDLE_FLAG_INHERIT = 0x00000001
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    STARTF_USESTDHANDLES = 0x00000100

    if not hasattr(kernel32, "CreatePseudoConsole"):
        kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePseudoConsole not available")

    kernel32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.InitializeProcThreadAttributeList.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel32.DeleteProcThreadAttributeList.restype = None

    if use_restricted_token:
        advapi32.CreateProcessAsUserW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOEXW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    else:
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOEXW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = True

    in_read = wintypes.HANDLE()
    in_write = wintypes.HANDLE()
    out_read = wintypes.HANDLE()
    out_write = wintypes.HANDLE()
    if not kernel32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), ctypes.byref(sa), 0):
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePipe failed (stdin)")
    if not kernel32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), ctypes.byref(sa), 0):
        kernel32.CloseHandle(in_read)
        kernel32.CloseHandle(in_write)
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePipe failed (stdout)")
    kernel32.SetHandleInformation(in_write, HANDLE_FLAG_INHERIT, 0)
    kernel32.SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0)

    h_pc = ctypes.c_void_p()
    size = COORD(cols, rows)
    create_pc_result = kernel32.CreatePseudoConsole(size, in_read, out_write, 0, ctypes.byref(h_pc))
    if _pty_debug_enabled():
        _pty_debug(f"conpty CreatePseudoConsole result={int(create_pc_result)} h_pc={int(h_pc.value or 0)} winerr={ctypes.get_last_error()}")
    if create_pc_result != 0:
        kernel32.CloseHandle(in_read)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
        kernel32.CloseHandle(out_write)
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePseudoConsole failed")
    keep_pipes = os.environ.get("CONPTY_KEEP_PIPES", "").strip().lower() in ("1", "true", "yes", "on")
    in_read_dbg = in_read if keep_pipes else None
    out_write_dbg = out_write if keep_pipes else None
    if not keep_pipes:
        kernel32.CloseHandle(in_read)
        kernel32.CloseHandle(out_write)
    elif _pty_debug_enabled():
        _pty_debug("conpty keep pipes enabled (CONPTY_KEEP_PIPES)")

    attr_list_size = ctypes.c_size_t()
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_list_size))
    # Ensure proper alignment for PROC_THREAD_ATTRIBUTE_LIST (pointer-aligned).
    attr_align = ctypes.alignment(ctypes.c_void_p)
    attr_list_buf = ctypes.create_string_buffer(attr_list_size.value + attr_align)
    attr_buf_addr = ctypes.addressof(attr_list_buf)
    attr_aligned_addr = (attr_buf_addr + (attr_align - 1)) & ~(attr_align - 1)
    attr_list_ptr = ctypes.c_void_p(attr_aligned_addr)
    if _pty_debug_enabled():
        _pty_debug(
            "conpty attr_list "
            f"size={int(attr_list_size.value)} align={int(attr_align)} "
            f"buf=0x{attr_buf_addr:x} aligned=0x{attr_aligned_addr:x}"
        )
    if not kernel32.InitializeProcThreadAttributeList(attr_list_ptr, 1, 0, ctypes.byref(attr_list_size)):
        kernel32.ClosePseudoConsole(h_pc)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        raise RuntimeError("InitializeProcThreadAttributeList failed")
    # For PSEUDOCONSOLE, lpValue expects the HPCON handle value (not a pointer to it).
    h_pc_value = ctypes.c_void_p(h_pc.value)
    update_attr_ok = kernel32.UpdateProcThreadAttribute(
        attr_list_ptr,
        0,
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
        h_pc_value,
        ctypes.sizeof(h_pc_value),
        None,
        None
    )
    if _pty_debug_enabled():
        _pty_debug(
            f"conpty UpdateProcThreadAttribute ok={bool(update_attr_ok)} "
            f"hpc={int(h_pc.value or 0)} winerr={ctypes.get_last_error()}"
        )
    if not update_attr_ok:
        kernel32.DeleteProcThreadAttributeList(attr_list_ptr)
        kernel32.ClosePseudoConsole(h_pc)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        raise RuntimeError("UpdateProcThreadAttribute failed")

    startup = STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    startup.lpAttributeList = attr_list_ptr
    startup.StartupInfo.lpDesktop = "WinSta0\\Default"
    if _pty_debug_enabled():
        try:
            startinfo_size = ctypes.sizeof(STARTUPINFO)
            startinfoex_size = ctypes.sizeof(STARTUPINFOEXW)
            attr_offset = startinfo_size
            _pty_debug(
                "conpty startupinfo "
                f"cb={int(startup.StartupInfo.cb)} "
                f"startinfo_size={int(startinfo_size)} "
                f"startinfoex_size={int(startinfoex_size)} "
                f"attr_offset={int(attr_offset)} "
                f"lp_attr=0x{int(ctypes.cast(startup.lpAttributeList, ctypes.c_void_p).value or 0):x} "
                f"attr_ptr=0x{int(ctypes.cast(attr_list_ptr, ctypes.c_void_p).value or 0):x}"
            )
        except Exception as exc:
            _pty_debug(f"conpty startupinfo debug error={exc}")
    create_flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
    if _pty_debug_enabled():
        _pty_debug(
            "conpty CreateProcess args "
            f"startup_cb={startup.StartupInfo.cb} "
            f"startup_flags={startup.StartupInfo.dwFlags} "
            f"startup_desktop={startup.StartupInfo.lpDesktop} "
            f"attr_list_ptr={int(ctypes.cast(startup.lpAttributeList, ctypes.c_void_p).value or 0)} "
            f"inherit_handles=False "
            f"flags={create_flags}"
        )

    command_line = ctypes.create_unicode_buffer(_windows_wrap_command(command, keep_open))
    _pty_debug(f"conpty start keep_open={keep_open} workdir={workdir} command_line={command_line.value}")

    proc_info = PROCESS_INFORMATION()
    if use_restricted_token:
        created = advapi32.CreateProcessAsUserW(
            restricted_token,
            None,
            command_line,
            None,
            None,
            False,
            create_flags,
            None,
            str(workdir),
            ctypes.byref(startup),
            ctypes.byref(proc_info)
        )
    else:
        created = kernel32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            create_flags,
            None,
            str(workdir),
            ctypes.byref(startup),
            ctypes.byref(proc_info)
        )

    kernel32.DeleteProcThreadAttributeList(attr_list_ptr)
    if restricted_token:
        kernel32.CloseHandle(restricted_token)

    if not created:
        kernel32.ClosePseudoConsole(h_pc)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
        raise RuntimeError("CreateProcessAsUserW failed (ConPTY)")

    _pty_debug(f"conpty created pid={proc_info.dwProcessId} keep_open={keep_open}")
    if _pty_debug_enabled():
        try:
            kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
            session_id = wintypes.DWORD()
            if kernel32.ProcessIdToSessionId(proc_info.dwProcessId, ctypes.byref(session_id)):
                _pty_debug(f"conpty session_id={int(session_id.value)}")
            else:
                _pty_debug(f"conpty session_id failed winerr={ctypes.get_last_error()}")
        except Exception as exc:
            _pty_debug(f"conpty session_id error={exc}")
        try:
            TH32CS_SNAPPROCESS = 0x00000002
            ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)
            class PROCESSENTRY32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ULONG_PTR),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
            kernel32.Process32NextW.restype = wintypes.BOOL
            snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot and snapshot != wintypes.HANDLE(-1).value:
                entry = PROCESSENTRY32()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
                has_conhost = False
                child_count = 0
                if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    while True:
                        if entry.th32ParentProcessID == proc_info.dwProcessId:
                            child_count += 1
                            exe_name = entry.szExeFile.lower()
                            if exe_name == "conhost.exe":
                                has_conhost = True
                        if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                            break
                kernel32.CloseHandle(snapshot)
                _pty_debug(f"conpty child_processes={child_count} conhost={str(has_conhost).lower()}")
            else:
                _pty_debug(f"conpty snapshot failed winerr={ctypes.get_last_error()}")
        except Exception as exc:
            _pty_debug(f"conpty snapshot error={exc}")
        try:
            kernel32.AttachConsole.argtypes = [wintypes.DWORD]
            kernel32.AttachConsole.restype = wintypes.BOOL
            kernel32.FreeConsole.argtypes = []
            kernel32.FreeConsole.restype = wintypes.BOOL
            attached = kernel32.AttachConsole(proc_info.dwProcessId)
            _pty_debug(f"conpty attach_console={str(bool(attached)).lower()} winerr={ctypes.get_last_error()}")
            if attached:
                kernel32.FreeConsole()
        except Exception as exc:
            _pty_debug(f"conpty attach_console error={exc}")
    job_handle = _windows_assign_job(kernel32, proc_info.hProcess)
    return kernel32, proc_info, h_pc, in_write, out_read, job_handle, in_read_dbg, out_write_dbg


def _run_windows_pty_oneshot(
    command: str,
    workdir: Path,
    timeout_sec: float,
    idle_timeout_ms: int,
    stdin_bytes: bytes,
    use_restricted_token: bool = True
) -> Tuple[int, str]:
    import ctypes
    from ctypes import wintypes

    kernel32, proc_info, h_pc, in_write, out_read, job_handle, in_read_dbg, out_write_dbg = _windows_start_conpty_process(
        command,
        workdir,
        keep_open=False,
        use_restricted_token=use_restricted_token
    )
    output_chunks: List[bytes] = []
    start_time = time.monotonic()
    last_output = start_time
    timed_out = False
    buffer = ctypes.create_string_buffer(4096)
    bytes_read = wintypes.DWORD()
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    if stdin_bytes:
        written = wintypes.DWORD()
        kernel32.WriteFile(in_write, stdin_bytes, len(stdin_bytes), ctypes.byref(written), None)

    while True:
        if timeout_sec is not None and timeout_sec > 0:
            if (time.monotonic() - start_time) >= timeout_sec:
                kernel32.TerminateProcess(proc_info.hProcess, 1)
                timed_out = True
                break
        if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
            output_chunks.append(b"\n[idle_timeout]\n")
            kernel32.TerminateProcess(proc_info.hProcess, 1)
            break

        wait_result = kernel32.WaitForSingleObject(proc_info.hProcess, 50)
        while True:
            available = wintypes.DWORD()
            if not kernel32.PeekNamedPipe(out_read, None, 0, None, ctypes.byref(available), None):
                break
            if available.value == 0:
                break
            to_read = min(len(buffer), available.value)
            if kernel32.ReadFile(out_read, buffer, to_read, ctypes.byref(bytes_read), None):
                if bytes_read.value > 0:
                    output_chunks.append(buffer.raw[:bytes_read.value])
                    last_output = time.monotonic()
        if wait_result == WAIT_OBJECT_0:
            break
        if wait_result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            break

    exit_code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
    kernel32.CloseHandle(out_read)
    kernel32.CloseHandle(in_write)
    if in_read_dbg:
        kernel32.CloseHandle(in_read_dbg)
    if out_write_dbg:
        kernel32.CloseHandle(out_write_dbg)
    # Close job handle BEFORE ClosePseudoConsole so
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE kills any remaining child processes
    if job_handle:
        kernel32.CloseHandle(job_handle)
    _safe_close_pseudo_console(kernel32, h_pc, label="conpty oneshot")
    kernel32.CloseHandle(proc_info.hThread)
    kernel32.CloseHandle(proc_info.hProcess)

    output = _decode_output_bytes(b"".join(output_chunks))
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout_sec, output=output)
    return int(exit_code.value), output


def _start_windows_pty_persistent(
    command: str,
    workdir: Path,
    stdin_bytes: bytes,
    idle_timeout_ms: int,
    buffer_size: int,
    use_restricted_token: bool = True
) -> PtyProcess:
    import ctypes
    from ctypes import wintypes

    launch_command = ""
    kernel32, proc_info, h_pc, in_write, out_read, job_handle, in_read_dbg, out_write_dbg = _windows_start_conpty_process(
        launch_command,
        workdir,
        keep_open=True,
        use_restricted_token=use_restricted_token
    )
    if _pty_debug_enabled():
        _pty_debug(
            "conpty launch shell "
            f"command_len={len(command or '')} stdin_len={len(stdin_bytes or b'')}"
        )
        try:
            file_type_out = kernel32.GetFileType(out_read)
            file_type_in = kernel32.GetFileType(in_write)
            _pty_debug(f"conpty file_type out={file_type_out} in={file_type_in}")
        except Exception as exc:
            _pty_debug(f"conpty file_type error={exc}")

    _conpty_cleanup_lock = threading.Lock()
    _conpty_cleaned_up = [False]

    def _writer(data: bytes) -> int:
        written = wintypes.DWORD()
        if kernel32.WriteFile(in_write, data, len(data), ctypes.byref(written), None):
            endswith_crlf = data.endswith(b"\r\n")
            _pty_debug(
                f"conpty write ok bytes={int(written.value)} endswith_crlf={endswith_crlf}"
            )
            _pty_debug(f"conpty write tail_hex={_tail_bytes_hex(data)}")
            if _pty_debug_enabled():
                if in_read_dbg:
                    in_available = wintypes.DWORD()
                    if kernel32.PeekNamedPipe(in_read_dbg, None, 0, None, ctypes.byref(in_available), None):
                        _pty_debug(f"conpty in_read_available={int(in_available.value)}")
                    else:
                        _pty_debug(f"conpty in_read_peek_failed winerr={ctypes.get_last_error()}")
                available = wintypes.DWORD()
                if kernel32.PeekNamedPipe(out_read, None, 0, None, ctypes.byref(available), None):
                    _pty_debug(f"conpty write peek_available={int(available.value)}")
                else:
                    _pty_debug(f"conpty write peek_failed winerr={ctypes.get_last_error()}")
                exit_code = wintypes.DWORD()
                kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
                wait_result = kernel32.WaitForSingleObject(proc_info.hProcess, 0)
                _pty_debug(f"conpty write proc_wait={int(wait_result)} exit_code={int(exit_code.value)}")
            return int(written.value)
        _pty_debug(f"conpty write failed winerr={ctypes.get_last_error()}")
        return 0

    def _terminator() -> None:
        if _pty_debug_enabled():
            _pty_debug(f"conpty terminator enter pid={proc_info.dwProcessId}")

        # Guard against double-cleanup (reader loop may also clean up)
        with _conpty_cleanup_lock:
            already = _conpty_cleaned_up[0]
            _conpty_cleaned_up[0] = True
        if already:
            try:
                kernel32.TerminateProcess(proc_info.hProcess, 1)
            except Exception:
                pass
            if _pty_debug_enabled():
                _pty_debug(f"conpty terminator skipped (already cleaned up) pid={proc_info.dwProcessId}")
            return

        def _log_call(name: str, ok: Optional[bool] = None) -> None:
            if not _pty_debug_enabled():
                return
            if ok is None:
                _pty_debug(f"conpty terminator {name}")
                return
            if ok:
                _pty_debug(f"conpty terminator {name} ok")
            else:
                err = ctypes.get_last_error()
                _pty_debug(f"conpty terminator {name} failed winerr={err}")

        # Kill all processes in the job object first (including grandchildren)
        if job_handle:
            try:
                kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel32.TerminateJobObject.restype = wintypes.BOOL
                _log_call("TerminateJobObject enter")
                ok = bool(kernel32.TerminateJobObject(job_handle, 1))
                _log_call("TerminateJobObject exit", ok)
            except Exception as exc:
                if _pty_debug_enabled():
                    _pty_debug(f"conpty terminator TerminateJobObject error={exc}")

        _log_call("TerminateProcess enter")
        ok = bool(kernel32.TerminateProcess(proc_info.hProcess, 1))
        _log_call("TerminateProcess exit", ok)

        _log_call("CloseHandle out_read enter")
        ok = bool(kernel32.CloseHandle(out_read))
        _log_call("CloseHandle out_read exit", ok)

        _log_call("CloseHandle in_write enter")
        ok = bool(kernel32.CloseHandle(in_write))
        _log_call("CloseHandle in_write exit", ok)

        if in_read_dbg:
            _log_call("CloseHandle in_read_dbg enter")
            ok = bool(kernel32.CloseHandle(in_read_dbg))
            _log_call("CloseHandle in_read_dbg exit", ok)
        if out_write_dbg:
            _log_call("CloseHandle out_write_dbg enter")
            ok = bool(kernel32.CloseHandle(out_write_dbg))
            _log_call("CloseHandle out_write_dbg exit", ok)

        # Close job handle BEFORE ClosePseudoConsole so
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE kills any remaining child processes
        if job_handle:
            _log_call("CloseHandle job_handle enter")
            ok = bool(kernel32.CloseHandle(job_handle))
            _log_call("CloseHandle job_handle exit", ok)

        # ClosePseudoConsole with timeout protection
        _safe_close_pseudo_console(kernel32, h_pc, label="conpty terminator")

        _log_call("CloseHandle hThread enter")
        ok = bool(kernel32.CloseHandle(proc_info.hThread))
        _log_call("CloseHandle hThread exit", ok)

        _log_call("CloseHandle hProcess enter")
        ok = bool(kernel32.CloseHandle(proc_info.hProcess))
        _log_call("CloseHandle hProcess exit", ok)
        if _pty_debug_enabled():
            _pty_debug(f"conpty terminator exit pid={proc_info.dwProcessId}")

    pty_proc = PtyProcess(
        session_id=_get_session_id(),
        command=command,
        pty_enabled=True,
        buffer_size=buffer_size,
        idle_timeout_ms=idle_timeout_ms,
        writer=_writer,
        terminator=_terminator,
        pty_mode="persistent"
    )
    _attach_pty_sse_emitter(pty_proc)

    def _reader_loop() -> None:
        buffer = ctypes.create_string_buffer(4096)
        bytes_read = wintypes.DWORD()
        last_output = time.monotonic()
        logged_first_chunk = False
        last_diag = time.monotonic()
        last_available = 0
        logged_eof = False
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102
        if _pty_debug_enabled():
            _pty_debug(f"conpty reader start pid={proc_info.dwProcessId}")
        raw_blocking = os.environ.get("PTY_CONPTY_BLOCKING_READ")
        use_blocking_read = False
        if raw_blocking is not None:
            if str(raw_blocking).strip().lower() in ("1", "true", "yes", "on"):
                use_blocking_read = True
        if _pty_debug_enabled():
            _pty_debug(f"conpty read mode={'blocking' if use_blocking_read else 'peek'}")
            if use_blocking_read:
                _pty_debug("conpty blocking read start")
        if use_blocking_read and _pty_debug_enabled():
            def _diag_loop() -> None:
                ERROR_BROKEN_PIPE = 109
                ERROR_INVALID_HANDLE = 6
                _pty_debug("conpty diag loop enter")
                while not pty_proc.stop_event.is_set():
                    time.sleep(2.0)
                    try:
                        available = wintypes.DWORD()
                        if not kernel32.PeekNamedPipe(out_read, None, 0, None, ctypes.byref(available), None):
                            err = ctypes.get_last_error()
                            _pty_debug(f"conpty diag peek failed winerr={err}")
                            if err in (ERROR_BROKEN_PIPE, ERROR_INVALID_HANDLE):
                                break
                            continue
                        _pty_debug(f"conpty diag peek available={int(available.value)}")
                        if in_read_dbg:
                            in_available = wintypes.DWORD()
                            if kernel32.PeekNamedPipe(in_read_dbg, None, 0, None, ctypes.byref(in_available), None):
                                _pty_debug(f"conpty diag in_read_available={int(in_available.value)}")
                            else:
                                _pty_debug(f"conpty diag in_read_peek_failed winerr={ctypes.get_last_error()}")
                    except Exception as exc:
                        _pty_debug(f"conpty diag error={exc}")
                        break

            diag_thread = threading.Thread(
                target=_diag_loop,
                name=f"conpty-diag-{pty_proc.id}",
                daemon=True
            )
            diag_thread.start()
            _pty_debug("conpty diag thread started")
        while not pty_proc.stop_event.is_set():
            wait_result = kernel32.WaitForSingleObject(proc_info.hProcess, 50)
            if use_blocking_read:
                if kernel32.ReadFile(out_read, buffer, len(buffer), ctypes.byref(bytes_read), None):
                    if bytes_read.value > 0:
                        pty_proc.append_output(buffer.raw[:bytes_read.value])
                        if not logged_first_chunk:
                            _pty_debug(f"conpty read first_chunk bytes={bytes_read.value}")
                            logged_first_chunk = True
                        last_output = time.monotonic()
                    elif not logged_eof:
                        logged_eof = True
                        _pty_debug("conpty read eof (0 bytes)")
                else:
                    _pty_debug(f"conpty read failed winerr={ctypes.get_last_error()}")
            else:
                while True:
                    available = wintypes.DWORD()
                    if not kernel32.PeekNamedPipe(out_read, None, 0, None, ctypes.byref(available), None):
                        _pty_debug(f"conpty peek failed winerr={ctypes.get_last_error()}")
                        break
                    if available.value == 0:
                        last_available = 0
                        break
                    last_available = int(available.value)
                    to_read = min(len(buffer), available.value)
                    if kernel32.ReadFile(out_read, buffer, to_read, ctypes.byref(bytes_read), None):
                        if bytes_read.value > 0:
                            pty_proc.append_output(buffer.raw[:bytes_read.value])
                            if not logged_first_chunk:
                                _pty_debug(f"conpty read first_chunk bytes={bytes_read.value}")
                                logged_first_chunk = True
                            last_output = time.monotonic()
                    else:
                        _pty_debug(f"conpty read failed winerr={ctypes.get_last_error()}")
            if _pty_debug_enabled() and (time.monotonic() - last_diag) >= 2.0:
                exit_code = wintypes.DWORD()
                kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
                wait_out = kernel32.WaitForSingleObject(out_read, 0)
                peek_available = wintypes.DWORD()
                peek_read = wintypes.DWORD()
                peek_byte_hex = ""
                try:
                    peek_buf = ctypes.create_string_buffer(1)
                    if kernel32.PeekNamedPipe(out_read, peek_buf, 1, ctypes.byref(peek_read), ctypes.byref(peek_available), None):
                        if peek_read.value:
                            peek_byte_hex = peek_buf.raw[: peek_read.value].hex()
                    else:
                        _pty_debug(f"conpty peek(diag) failed winerr={ctypes.get_last_error()}")
                except Exception as exc:
                    _pty_debug(f"conpty peek(diag) error={exc}")
                _pty_debug(
                    "conpty poll "
                    f"wait_result={wait_result} idle_ms={int((time.monotonic() - last_output) * 1000)} "
                    f"available={last_available} wait_out={wait_out} "
                    f"peek_available={int(peek_available.value)} peek_read={int(peek_read.value)} peek_byte_hex={peek_byte_hex} "
                    f"exit_code={int(exit_code.value)}"
                )
                last_diag = time.monotonic()
            if wait_result == WAIT_OBJECT_0:
                break
            if wait_result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
                _pty_debug(f"conpty wait unexpected result={wait_result}")
                break
            if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
                pty_proc.append_output(b"\n[idle_timeout]\n")
                _pty_debug("conpty idle_timeout reached")
                kernel32.TerminateProcess(proc_info.hProcess, 1)
                break
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
        pty_proc.mark_exited(int(exit_code.value))
        _pty_debug(f"conpty exited code={int(exit_code.value)}")

        # Guard against double-cleanup (_terminator may also clean up)
        with _conpty_cleanup_lock:
            already = _conpty_cleaned_up[0]
            _conpty_cleaned_up[0] = True
        if already:
            _pty_debug("conpty reader cleanup skipped (already cleaned up)")
            return

        kernel32.CloseHandle(out_read)
        kernel32.CloseHandle(in_write)
        if in_read_dbg:
            kernel32.CloseHandle(in_read_dbg)
        if out_write_dbg:
            kernel32.CloseHandle(out_write_dbg)
        # Close job handle BEFORE ClosePseudoConsole so
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE kills any remaining child processes
        if job_handle:
            kernel32.CloseHandle(job_handle)
        _safe_close_pseudo_console(kernel32, h_pc, label="conpty reader")
        kernel32.CloseHandle(proc_info.hThread)
        kernel32.CloseHandle(proc_info.hProcess)

    thread = threading.Thread(target=_reader_loop, name=f"conpty-reader-{pty_proc.id}", daemon=True)
    pty_proc.reader_thread = thread
    thread.start()
    initial_bytes = b""
    if command:
        initial_text = _normalize_windows_stdin(command)
        initial_bytes += _encode_stdin(initial_text)
        if _pty_debug_enabled():
            endswith_crlf = initial_bytes.endswith(b"\r\n")
            _pty_debug(
                "conpty initial command "
                f"len={len(initial_bytes)} endswith_crlf={endswith_crlf} "
                f"tail_hex={_tail_bytes_hex(initial_bytes)}"
            )
    if stdin_bytes:
        initial_bytes += stdin_bytes
    if initial_bytes:
        _writer(initial_bytes)
    return pty_proc


def _start_windows_pipe_persistent(
    command: str,
    workdir: Path,
    stdin_bytes: bytes,
    idle_timeout_ms: int,
    buffer_size: int
) -> PtyProcess:
    import ctypes
    from ctypes import wintypes

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL)
        ]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE)
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD)
        ]

    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    STARTF_USESTDHANDLES = 0x00000100
    HANDLE_FLAG_INHERIT = 0x00000001

    restricted_token, kernel32, advapi32 = _windows_create_restricted_token()
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFO),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = True

    stdin_read = wintypes.HANDLE()
    stdin_write = wintypes.HANDLE()
    stdout_read = wintypes.HANDLE()
    stdout_write = wintypes.HANDLE()
    if not kernel32.CreatePipe(ctypes.byref(stdin_read), ctypes.byref(stdin_write), ctypes.byref(sa), 0):
        kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePipe failed (stdin)")
    if not kernel32.CreatePipe(ctypes.byref(stdout_read), ctypes.byref(stdout_write), ctypes.byref(sa), 0):
        kernel32.CloseHandle(stdin_read)
        kernel32.CloseHandle(stdin_write)
        kernel32.CloseHandle(restricted_token)
        raise RuntimeError("CreatePipe failed (stdout)")
    kernel32.SetHandleInformation(stdin_write, HANDLE_FLAG_INHERIT, 0)
    kernel32.SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0)

    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(STARTUPINFO)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdInput = stdin_read
    startup.hStdOutput = stdout_write
    startup.hStdError = stdout_write

    command_line = ctypes.create_unicode_buffer(_windows_wrap_command(command, keep_open=True))

    proc_info = PROCESS_INFORMATION()
    created = advapi32.CreateProcessAsUserW(
        restricted_token,
        None,
        command_line,
        None,
        None,
        True,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        None,
        str(workdir),
        ctypes.byref(startup),
        ctypes.byref(proc_info)
    )

    kernel32.CloseHandle(restricted_token)
    kernel32.CloseHandle(stdin_read)
    kernel32.CloseHandle(stdout_write)

    if not created:
        kernel32.CloseHandle(stdin_write)
        kernel32.CloseHandle(stdout_read)
        raise RuntimeError("CreateProcessAsUserW failed (pipe)")

    job_handle = _windows_assign_job(kernel32, proc_info.hProcess)

    def _writer(data: bytes) -> int:
        written = wintypes.DWORD()
        if kernel32.WriteFile(stdin_write, data, len(data), ctypes.byref(written), None):
            return int(written.value)
        return 0

    def _terminator() -> None:
        kernel32.TerminateProcess(proc_info.hProcess, 1)
        kernel32.CloseHandle(stdout_read)
        kernel32.CloseHandle(stdin_write)
        if job_handle:
            kernel32.CloseHandle(job_handle)
        kernel32.CloseHandle(proc_info.hThread)
        kernel32.CloseHandle(proc_info.hProcess)

    pty_proc = PtyProcess(
        session_id=_get_session_id(),
        command=command,
        pty_enabled=False,
        buffer_size=buffer_size,
        idle_timeout_ms=idle_timeout_ms,
        writer=_writer,
        terminator=_terminator,
        pty_mode="persistent"
    )
    _attach_pty_sse_emitter(pty_proc)

    def _reader_loop() -> None:
        buffer = ctypes.create_string_buffer(4096)
        bytes_read = wintypes.DWORD()
        last_output = time.monotonic()
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102
        while not pty_proc.stop_event.is_set():
            wait_result = kernel32.WaitForSingleObject(proc_info.hProcess, 50)
            while True:
                available = wintypes.DWORD()
                if not kernel32.PeekNamedPipe(stdout_read, None, 0, None, ctypes.byref(available), None):
                    break
                if available.value == 0:
                    break
                to_read = min(len(buffer), available.value)
                if kernel32.ReadFile(stdout_read, buffer, to_read, ctypes.byref(bytes_read), None):
                    if bytes_read.value > 0:
                        pty_proc.append_output(buffer.raw[:bytes_read.value])
                        last_output = time.monotonic()
            if wait_result == WAIT_OBJECT_0:
                break
            if wait_result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
                break
            if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
                pty_proc.append_output(b"\n[idle_timeout]\n")
                kernel32.TerminateProcess(proc_info.hProcess, 1)
                break
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code))
        pty_proc.mark_exited(int(exit_code.value))
        kernel32.CloseHandle(stdout_read)
        kernel32.CloseHandle(stdin_write)
        if job_handle:
            kernel32.CloseHandle(job_handle)
        kernel32.CloseHandle(proc_info.hThread)
        kernel32.CloseHandle(proc_info.hProcess)

    thread = threading.Thread(target=_reader_loop, name=f"pipe-reader-{pty_proc.id}", daemon=True)
    pty_proc.reader_thread = thread
    thread.start()
    if stdin_bytes:
        _writer(stdin_bytes)
    return pty_proc


def _start_windows_unrestricted_pipe_persistent(
    command: str,
    workdir: Path,
    stdin_bytes: bytes,
    idle_timeout_ms: int,
    buffer_size: int
) -> PtyProcess:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    command_line = _windows_wrap_command(command, keep_open=True)
    proc = subprocess.Popen(
        command_line,
        cwd=str(workdir),
        shell=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NEW_PROCESS_GROUP
    )

    stdout_handle = None
    if proc.stdout is not None:
        try:
            stdout_handle = msvcrt.get_osfhandle(proc.stdout.fileno())
        except Exception:
            stdout_handle = None

    def _writer(data: bytes) -> int:
        if not proc.stdin:
            return 0
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
            return len(data)
        except Exception:
            return 0

    def _terminator() -> None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

    pty_proc = PtyProcess(
        session_id=_get_session_id(),
        command=command,
        pty_enabled=False,
        buffer_size=buffer_size,
        idle_timeout_ms=idle_timeout_ms,
        writer=_writer,
        terminator=_terminator,
        pty_mode="persistent"
    )
    _attach_pty_sse_emitter(pty_proc)

    def _reader_loop() -> None:
        buffer = ctypes.create_string_buffer(4096)
        bytes_read = wintypes.DWORD()
        last_output = time.monotonic()
        if stdout_handle is None:
            pty_proc.mark_exited(proc.poll())
            return
        while not pty_proc.stop_event.is_set():
            available = wintypes.DWORD()
            if kernel32.PeekNamedPipe(stdout_handle, None, 0, None, ctypes.byref(available), None) and available.value:
                to_read = min(len(buffer), available.value)
                if kernel32.ReadFile(stdout_handle, buffer, to_read, ctypes.byref(bytes_read), None):
                    if bytes_read.value > 0:
                        pty_proc.append_output(buffer.raw[:bytes_read.value])
                        last_output = time.monotonic()
            if proc.poll() is not None:
                break
            if idle_timeout_ms and (time.monotonic() - last_output) * 1000.0 >= idle_timeout_ms:
                pty_proc.append_output(b"\n[idle_timeout]\n")
                try:
                    proc.terminate()
                except Exception:
                    pass
                break
            time.sleep(0.05)
        pty_proc.mark_exited(proc.poll())
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    thread = threading.Thread(target=_reader_loop, name=f"pipe-reader-{pty_proc.id}", daemon=True)
    pty_proc.reader_thread = thread
    thread.start()
    if stdin_bytes:
        _writer(stdin_bytes)
    return pty_proc


def _ensure_shell_allowlist_entry(command_name: str) -> None:
    if not command_name:
        return
    config = get_tool_config()
    allowlist = list(config.get("shell", {}).get("allowlist", []) or [])
    allowset = {str(item).lower() for item in allowlist}
    if command_name.lower() in allowset:
        return
    allowlist.append(command_name)
    try:
        update_tool_config({"shell": {"allowlist": allowlist}})
    except Exception:
        pass


def _ensure_shell_unrestricted_allowlist_entry(command_name: str) -> None:
    if not command_name:
        return
    config = get_tool_config()
    allowlist = list(config.get("shell", {}).get("unrestricted_allowlist", []) or [])
    allowset = {str(item).lower() for item in allowlist}
    if command_name.lower() in allowset:
        return
    allowlist.append(command_name)
    try:
        update_tool_config({"shell": {"unrestricted_allowlist": allowlist}})
    except Exception:
        pass


async def _wait_for_permission(request_id: Optional[int], timeout_sec: float) -> str:
    if not request_id:
        return "denied"
    start = time.monotonic()
    while True:
        try:
            from repositories.permission_repository import get_permission_request
            record = get_permission_request(request_id)
            if record and record.get("status") and record["status"] != "pending":
                return record["status"]
        except Exception:
            pass

        if timeout_sec is not None and (time.monotonic() - start) >= timeout_sec:
            try:
                from repositories.permission_repository import update_permission_request
                update_permission_request(request_id, "timeout")
            except Exception:
                pass
            return "timeout"

        await asyncio.sleep(0.5)


from .file_tools import ListFilesTool, ReadFileTool, WriteFileTool


class RunShellTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "run_shell"
        self.description = (
            "Run a shell command within the work path. "
            "Supports mode=auto|oneshot|ephemeral|persistent. "
            "Persistent mode returns a pty_id and supports action=send|read|close|list. "
            "Optional args: stdin, timeout_sec (s), idle_timeout (ms), "
            "max_output (chars), buffer_size (bytes)."
        )
        self.parameters = [
            ToolParameter(
                name="command",
                type="string",
                description="Shell command to run.",
                required=False
            ),
            ToolParameter(
                name="mode",
                type="string",
                description="auto, oneshot, ephemeral, or persistent (default auto).",
                required=False
            ),
            ToolParameter(
                name="action",
                type="string",
                description="For persistent sessions: send, read, close, list.",
                required=False
            ),
            ToolParameter(
                name="pty_id",
                type="string",
                description="Persistent PTY session id.",
                required=False
            ),
            ToolParameter(
                name="stdin",
                type="string",
                description="Stdin content to write (ephemeral bootstrap or send).",
                required=False
            ),
            ToolParameter(
                name="cwd",
                type="string",
                description="Working directory (relative to the work path).",
                required=False
            ),
            ToolParameter(
                name="timeout_sec",
                type="number",
                description="Timeout in seconds.",
                required=False
            ),
            ToolParameter(
                name="idle_timeout",
                type="number",
                description="Idle timeout in milliseconds (default 120000 for ephemeral, 0 for persistent).",
                required=False
            ),
            ToolParameter(
                name="max_output",
                type="number",
                description="Max combined stdout/stderr characters to return.",
                required=False
            ),
            ToolParameter(
                name="buffer_size",
                type="number",
                description="Persistent buffer size in bytes (default ~2MB).",
                required=False
            ),
            ToolParameter(
                name="cursor",
                type="number",
                description="Read cursor for persistent sessions.",
                required=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        # Normalize persistent PTY interactions: if a pty_id is provided without an action,
        # infer send/read to avoid accidentally creating a new PTY session.
        if isinstance(data, dict):
            action_hint = str(data.get("action") or "").strip().lower()
            if not action_hint and data.get("pty_id"):
                if data.get("stdin") is not None or data.get("command") is not None:
                    data = dict(data)
                    data["action"] = "send"
                    if data.get("stdin") is None and data.get("command") is not None:
                        data["stdin"] = data.get("command")
                    data.pop("command", None)
                else:
                    data = dict(data)
                    data["action"] = "read"
            elif action_hint == "send" and data.get("stdin") is None and data.get("command") is not None:
                data = dict(data)
                data["stdin"] = data.get("command")
                data.pop("command", None)
        if _pty_debug_enabled():
            try:
                raw_preview = input_data if isinstance(input_data, str) else str(input_data)
                raw_preview = raw_preview.replace("\r", "\\r").replace("\n", "\\n")
                if len(raw_preview) > 400:
                    raw_preview = raw_preview[:400] + "...(truncated)"
                _pty_debug(f"run_shell input_preview={raw_preview}")
                _pty_debug(
                    "run_shell parsed "
                    f"mode={data.get('mode')} action={data.get('action')} "
                    f"pty={data.get('pty')} command={data.get('command')}"
                )
            except Exception as exc:
                _pty_debug(f"run_shell debug error={exc}")
        action = str(data.get("action") or "").strip().lower()
        mode_override: Optional[str] = None
        if action:
            if action != "list" and not data.get("pty_id"):
                # If action is set but no pty_id is provided:
                # - For send with a command, auto-start a persistent session.
                # - Otherwise, fall back to ephemeral when a command exists.
                if action == "send" and data.get("command"):
                    mode_override = "persistent"
                    action = ""
                elif data.get("command"):
                    action = ""
                else:
                    return "Missing pty_id."

        if action:
            session_id = _get_session_id()
            manager = get_pty_manager()
            if action == "list":
                items = manager.list(session_id)
                lines = [f"[pty_list count={len(items)}]"]
                for item in items:
                    lines.append(
                        f"- id={item.id} status={item.status} pty={str(item.pty_enabled).lower()} exit_code={item.exit_code}"
                    )
                return "\n".join(lines)
            pty_id = data.get("pty_id")
            if not pty_id:
                return "Missing pty_id."
            proc = manager.get(session_id, str(pty_id))
            if not proc:
                return "PTY not found."
            if action == "send":
                stdin_value = data.get("stdin")
                if _is_windows() and isinstance(stdin_value, str) and stdin_value:
                    stdin_value = _normalize_windows_stdin(stdin_value)
                if _pty_debug_enabled():
                    if isinstance(stdin_value, str):
                        endswith_lf = stdin_value.endswith("\n")
                        endswith_crlf = stdin_value.endswith("\r\n")
                        _pty_debug(
                            "run_shell send stdin "
                            f"type=str len={len(stdin_value)} "
                            f"endswith_lf={endswith_lf} endswith_crlf={endswith_crlf} "
                            "completion_tracking=false"
                        )
                    else:
                        _pty_debug(
                            "run_shell send stdin "
                            f"type={type(stdin_value).__name__} "
                            "completion_tracking=false"
                        )
                stdin_bytes = _encode_stdin(stdin_value)
                if _pty_debug_enabled():
                    endswith_crlf = stdin_bytes.endswith(b"\r\n")
                    _pty_debug(
                        "run_shell send bytes "
                        f"len={len(stdin_bytes)} endswith_crlf={endswith_crlf} "
                        f"tail_hex={_tail_bytes_hex(stdin_bytes)}"
                    )
                written = proc.write(stdin_bytes)
                _set_waiting_state(proc, False, None)
                header = _format_pty_header(proc, bytes_written=written)
                return header
            if action == "read":
                max_output = data.get("max_output")
                if max_output is None:
                    max_output = int(get_tool_config().get("shell", {}).get("max_output", 20000))
                else:
                    max_output = int(max_output)
                cursor = data.get("cursor")
                if cursor is not None:
                    try:
                        cursor = int(cursor)
                    except (TypeError, ValueError):
                        cursor = None
                if _pty_debug_enabled():
                    _pty_debug(
                        "run_shell read enter "
                        f"pty_id={proc.id} cursor={cursor} max_output={max_output} status={proc.status} "
                        "completion_tracking=false"
                    )
                read_start = time.monotonic()
                try:
                    chunk, new_cursor, reset = await asyncio.to_thread(proc.read, cursor, max_output)
                except Exception as exc:
                    if _pty_debug_enabled():
                        _pty_debug(f"run_shell read error pty_id={proc.id} error={exc}")
                    return f"[pty_id={proc.id} status=error] PTY read failed: {exc}"
                if _pty_debug_enabled():
                    elapsed = time.monotonic() - read_start
                    _pty_debug(
                        "run_shell read exit "
                        f"pty_id={proc.id} elapsed={elapsed:.3f}s chunk_len={len(chunk or '')} "
                        f"new_cursor={new_cursor} reset={str(reset).lower()} status={proc.status}"
                    )
                waiting_input, wait_reason = _update_waiting_state_from_chunk(proc, chunk or "")
                header = _format_pty_header(
                    proc,
                    cursor=new_cursor,
                    reset=reset
                )
                if wait_reason and "wait_reason=" not in header:
                    header = f"{header} wait_reason={wait_reason}"
                return f"{header}\n{chunk or ''}"
            if action == "close":
                if _pty_debug_enabled():
                    _pty_debug(f"run_shell close enter pty_id={pty_id}")
                    close_start = time.monotonic()
                close_timeout_sec = 0.5
                try:
                    raw_timeout = os.environ.get("PTY_CLOSE_TIMEOUT_SEC")
                    if raw_timeout is not None and str(raw_timeout).strip() != "":
                        close_timeout_sec = float(raw_timeout)
                except (TypeError, ValueError):
                    close_timeout_sec = 0.5
                if close_timeout_sec is not None and close_timeout_sec <= 0:
                    close_timeout_sec = None
                close_task = asyncio.create_task(asyncio.to_thread(manager.close, session_id, str(pty_id)))
                closed = False
                if close_timeout_sec is None:
                    closed = await close_task
                else:
                    try:
                        closed = await asyncio.wait_for(asyncio.shield(close_task), timeout=close_timeout_sec)
                    except asyncio.TimeoutError:
                        closed = False
                        if _pty_debug_enabled():
                            _pty_debug(
                                f"run_shell close timeout pty_id={pty_id} timeout_sec={close_timeout_sec}"
                            )
                if _pty_debug_enabled():
                    elapsed = time.monotonic() - close_start
                    _pty_debug(
                        f"run_shell close exit pty_id={pty_id} elapsed={elapsed:.3f}s closed={closed}"
                    )
                status = "closed" if closed else "closing"
                return f"[pty_id={pty_id} status={status}]"
            return f"Unknown action: {action}"

        command = data.get("command")
        mode = str(data.get("mode") or "auto").strip().lower()
        if mode_override:
            mode = mode_override
        if mode not in ("auto", "oneshot", "persistent", "ephemeral"):
            mode = "auto"
        if mode in ("auto", "oneshot"):
            mode = "ephemeral"
        if command is None:
            if mode in ("persistent", "ephemeral"):
                command = ""
            else:
                command = input_data
        if command is None:
            raise ValueError("Missing command.")
        command_text = str(command)
        persistent_bootstrap = mode in ("persistent", "ephemeral") and not command_text.strip()

        cmd_name = _extract_command_name(command_text)
        root = _get_root_path()
        if cmd_name == "rg":
            command = _rewrite_rg_command(command, root)
        tool_ctx = get_tool_context()
        agent_mode = _get_agent_mode()
        shell_unrestricted = bool(tool_ctx.get("shell_unrestricted"))
        allowlist = get_tool_config().get("shell", {}).get("allowlist", [])
        allowset = {str(item).lower() for item in allowlist}
        unrestricted_allowlist = get_tool_config().get("shell", {}).get("unrestricted_allowlist", [])
        unrestricted_allowset = {str(item).lower() for item in unrestricted_allowlist}

        reasons = []
        if agent_mode != "super":
            if not shell_unrestricted and not persistent_bootstrap and cmd_name not in allowset:
                reasons.append("Command not in allowlist.")
        if reasons:
            request_id = None
            try:
                from repositories.permission_repository import create_permission_request
                request_id = create_permission_request(
                    tool_name=self.name,
                    action="execute",
                    path=str(command),
                    reason=" ".join(reasons),
                    session_id=tool_ctx.get("session_id")
                )
            except Exception:
                request_id = None

            permission_timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, permission_timeout_sec)
            if status not in ("approved", "approved_once"):
                if status == "denied":
                    return "Permission denied."
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if status == "approved" and agent_mode == "default" and not shell_unrestricted and not persistent_bootstrap and cmd_name not in allowset:
                _ensure_shell_allowlist_entry(cmd_name)

        # NOTE: We intentionally do not enforce the "work path" restriction on
        # `cwd` for `run_shell`. The shell sandbox/unrestricted allowlist already
        # gates what can be executed; blocking `cwd` outside work_path makes it
        # impossible to run commands that legitimately need an external working
        # directory (e.g. OS temp), and current UX expects "allow" to proceed.
        cwd = data.get("cwd")
        if cwd:
            workdir = Path(str(cwd))
            if not workdir.is_absolute():
                workdir = root / workdir
            workdir = workdir.expanduser().resolve()
        else:
            workdir = root

        timeout_sec: Optional[float] = None
        timeout_ms = data.get("timeout")
        if timeout_ms is not None:
            try:
                timeout_ms = float(timeout_ms)
            except (TypeError, ValueError):
                timeout_ms = None
        if timeout_ms is not None and timeout_ms > 0:
            timeout_sec = timeout_ms / 1000.0
        else:
            timeout_value = data.get("timeout_sec")
            if timeout_value is not None:
                try:
                    timeout_sec = float(timeout_value)
                except (TypeError, ValueError):
                    timeout_sec = None
        if timeout_sec is not None and timeout_sec <= 0:
            timeout_sec = None
        if timeout_sec is None and mode != "persistent":
            try:
                timeout_sec = float(get_tool_config().get("shell", {}).get("timeout_sec", 120))
            except (TypeError, ValueError):
                timeout_sec = 120.0
        idle_timeout_ms = _resolve_idle_timeout_ms(mode, data.get("idle_timeout"))
        buffer_size = _resolve_buffer_size(data.get("buffer_size"))
        max_output = data.get("max_output")
        max_output = int(max_output) if max_output is not None else int(get_tool_config().get("shell", {}).get("max_output", 20000))

        stdin_value = data.get("stdin")
        if _is_windows() and isinstance(stdin_value, str) and stdin_value:
            stdin_value = _normalize_windows_stdin(stdin_value)
        stdin_bytes = _encode_stdin(stdin_value)
        pty_requested = data.get("pty")
        pty_policy = "auto"
        if pty_requested is not None:
            pty_policy = "force_pty" if _coerce_bool(pty_requested, False) else "force_pipe"
        pty_supported = _supports_pty()
        if pty_policy == "force_pty":
            if not pty_supported:
                return "PTY requested but not available on this platform."
            pty_enabled = True
            pty_fallback = False
        elif pty_policy == "force_pipe":
            pty_enabled = False
            pty_fallback = False
        else:
            pty_enabled = pty_supported
            pty_fallback = False
        if _pty_debug_enabled():
            _pty_debug(
                "run_shell pty_resolve "
                f"mode={mode} pty_requested={pty_requested} pty_policy={pty_policy} "
                f"pty_supported={pty_supported} pty_enabled={pty_enabled}"
            )

        def _maybe_auto_fallback(stage: str, exc: Exception) -> bool:
            nonlocal pty_enabled, pty_fallback
            if pty_policy != "auto" or not pty_enabled:
                return False
            if isinstance(exc, subprocess.TimeoutExpired):
                return False
            pty_enabled = False
            pty_fallback = True
            if _pty_debug_enabled():
                _pty_debug(
                    "run_shell auto_pty_fallback "
                    f"stage={stage} error={type(exc).__name__}: {exc}"
                )
            return True

        def _start_persistent(use_sandbox: bool) -> PtyProcess:
            _pty_debug(
                "run_shell start_persistent "
                f"use_sandbox={use_sandbox} pty_enabled={pty_enabled} pty_supported={pty_supported} "
                f"pty_fallback={pty_fallback} command={command}"
            )
            if _is_windows():
                if pty_enabled:
                    try:
                        return _start_windows_pty_persistent(
                            command,
                            workdir,
                            stdin_bytes,
                            idle_timeout_ms,
                            buffer_size,
                            use_restricted_token=_use_restricted_conpty(use_sandbox)
                        )
                    except Exception as exc:
                        if not _maybe_auto_fallback("persistent_start", exc):
                            raise
                if use_sandbox:
                    return _start_windows_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size)
                return _start_windows_unrestricted_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size)
            if pty_enabled:
                try:
                    return _start_posix_pty_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size, use_sandbox)
                except Exception as exc:
                    if not _maybe_auto_fallback("persistent_start", exc):
                        raise
            return _start_posix_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size, use_sandbox)

        try:
            if agent_mode == "super":
                pty_proc = _start_persistent(use_sandbox=False)
            elif cmd_name in unrestricted_allowset:
                pty_proc = _start_persistent(use_sandbox=False)
            else:
                pty_proc = _start_persistent(use_sandbox=True)
            pty_proc.pty_mode = mode
            manager = get_pty_manager()
            manager.register(pty_proc)
            chunk, cursor, _ = pty_proc.read(None, max_output)
            waiting_input, wait_reason = _update_waiting_state_from_chunk(pty_proc, chunk or "")
            snapshot = pty_proc.get_snapshot()
            seq = int(snapshot.get("seq") or 0)
            screen_hash = str(snapshot.get("screen_hash") or "")
            _pty_debug(f"run_shell persistent started pty_id={pty_proc.id} initial_chunk_len={len(chunk or '')}")
            header = _format_pty_header(pty_proc, cursor=cursor)
            if waiting_input and "waiting_input=true" not in header:
                header = f"{header} waiting_input=true"
            if wait_reason and "wait_reason=" not in header:
                header = f"{header} wait_reason={wait_reason}"
            if screen_hash and "screen_hash=" not in header:
                header = f"{header} screen_hash={screen_hash}"
            if seq and "seq=" not in header:
                header = f"{header} seq={seq}"
            if pty_fallback:
                header = f"{header} pty_fallback=true"
            if mode == "persistent":
                message_id = _create_persistent_pty_message(pty_proc)
                if message_id:
                    header = f"{header} pty_message_id={message_id} pty_live=true"
                return header
            return f"{header}\n{chunk or ''}"
        except Exception as exc:
            request_id = None
            try:
                from repositories.permission_repository import create_permission_request
                request_id = create_permission_request(
                    tool_name=self.name,
                    action="execute_unrestricted",
                    path=str(command),
                    reason=f"Sandbox execution failed: {str(exc)}",
                    session_id=tool_ctx.get("session_id")
                )
            except Exception:
                request_id = None

            permission_timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, permission_timeout_sec)
            if status not in ("approved", "approved_once"):
                if status == "denied":
                    return "Permission denied."
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if status == "approved" and agent_mode == "default" and not shell_unrestricted and not persistent_bootstrap and cmd_name not in unrestricted_allowset:
                _ensure_shell_unrestricted_allowlist_entry(cmd_name)

            pty_proc = _start_persistent(use_sandbox=False)
            pty_proc.pty_mode = mode
            manager = get_pty_manager()
            manager.register(pty_proc)
            chunk, cursor, _ = pty_proc.read(None, max_output)
            waiting_input, wait_reason = _update_waiting_state_from_chunk(pty_proc, chunk or "")
            snapshot = pty_proc.get_snapshot()
            seq = int(snapshot.get("seq") or 0)
            screen_hash = str(snapshot.get("screen_hash") or "")
            header = _format_pty_header(pty_proc, cursor=cursor)
            if waiting_input and "waiting_input=true" not in header:
                header = f"{header} waiting_input=true"
            if wait_reason and "wait_reason=" not in header:
                header = f"{header} wait_reason={wait_reason}"
            if screen_hash and "screen_hash=" not in header:
                header = f"{header} screen_hash={screen_hash}"
            if seq and "seq=" not in header:
                header = f"{header} seq={seq}"
            if pty_fallback:
                header = f"{header} pty_fallback=true"
            if mode == "persistent":
                message_id = _create_persistent_pty_message(pty_proc)
                if message_id:
                    header = f"{header} pty_message_id={message_id} pty_live=true"
                return header
            return f"{header}\n{chunk or ''}"

__all__ = ["RunShellTool"]
