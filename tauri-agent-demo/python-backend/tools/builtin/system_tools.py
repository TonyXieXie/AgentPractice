import asyncio
import ast as py_ast
import difflib
import json
import os
import shlex
import subprocess
import sys
import locale
import select
import threading
import time
from pathlib import Path
from typing import Any, Dict, Tuple, Optional, List, Set

import httpx

from ..base import Tool, ToolParameter
from ..config import get_tool_config, update_tool_config
from ..context import get_tool_context
from ..pty_manager import get_pty_manager, PtyProcess, DEFAULT_BUFFER_SIZE, _decode_output_bytes
from app_config import get_app_config
from ast_settings import get_ast_settings
from ast_file_filter import collect_ast_files
from ws_hub import get_ws_hub

try:
    import pty as posix_pty
except Exception:
    posix_pty = None


def _get_root_path() -> Path:
    tool_ctx = get_tool_context()
    root = tool_ctx.get("work_path") or get_tool_config().get("project_root") or os.getcwd()
    return Path(root).expanduser().resolve()


def _get_allowed_roots() -> list:
    tool_ctx = get_tool_context()
    roots = []
    primary = tool_ctx.get("work_path") or get_tool_config().get("project_root") or os.getcwd()
    if primary:
        roots.append(Path(primary).expanduser().resolve())
    extras = tool_ctx.get("extra_work_paths")
    if isinstance(extras, (list, tuple)):
        for raw in extras:
            if not raw:
                continue
            try:
                roots.append(Path(raw).expanduser().resolve())
            except Exception:
                continue
    seen = set()
    unique = []
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _resolve_rg_executable(root: Path) -> str:
    override = os.environ.get("RG_EXE") or os.environ.get("RIPGREP_EXE")
    if override:
        return override
    rel_path = Path("tools") / "rg" / ("rg.exe" if os.name == "nt" else "rg")
    candidates = [root / rel_path]
    project_root = get_tool_config().get("project_root")
    if project_root:
        try:
            candidates.append(Path(project_root).expanduser().resolve() / rel_path)
        except Exception:
            pass
    try:
        candidates.append(Path(__file__).resolve().parents[3] / rel_path)
    except Exception:
        pass
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "rg"


def _is_within_root(path: Path, root: Path) -> bool:
    root_str = str(root).lower()
    path_str = str(path).lower()
    if path_str == root_str:
        return True
    return path_str.startswith(root_str + os.sep)


def _log_permission_request(tool_name: str, action: str, path: Path, reason: str) -> Optional[int]:
    try:
        from database import db
        tool_ctx = get_tool_context()
        session_id = tool_ctx.get("session_id")
        return db.create_permission_request(
            tool_name=tool_name,
            action=action,
            path=str(path),
            reason=reason,
            session_id=session_id
        )
    except Exception:
        return None


def _get_agent_mode() -> str:
    tool_ctx = get_tool_context()
    mode = tool_ctx.get("agent_mode") or "default"
    mode = str(mode).lower()
    if mode not in ("default", "super"):
        mode = "default"
    return mode


def _get_session_id() -> str:
    tool_ctx = get_tool_context()
    session_id = tool_ctx.get("session_id")
    if session_id is None or session_id == "":
        return "global"
    return str(session_id)


def _resolve_path(raw_path: str, tool_name: str, action: str) -> Path:
    roots = _get_allowed_roots()
    root = roots[0] if roots else _get_root_path()
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not any(_is_within_root(path, allowed_root) for allowed_root in roots):
        mode = _get_agent_mode()
        if mode == "super":
            return path
        request_id = _log_permission_request(
            tool_name=tool_name,
            action=action,
            path=path,
            reason="Path outside work path."
        )
        suffix = f" Request ID: {request_id}" if request_id else ""
        raise PermissionError(f"Permission required for paths outside work path.{suffix}")
    return path


def _maybe_create_snapshot() -> None:
    tool_ctx = get_tool_context()
    session_id = tool_ctx.get("session_id")
    message_id = tool_ctx.get("message_id")
    if not session_id or not message_id:
        if not session_id:
            return
        try:
            from database import db
            message_id = db.get_latest_assistant_message_id(str(session_id))
        except Exception:
            return
        if not message_id:
            return
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return

    work_path = tool_ctx.get("work_path")
    root = Path(work_path).expanduser().resolve() if work_path else _get_root_path()
    try:
        from ghost_snapshot import ensure_snapshot
        ensure_snapshot(str(session_id), message_id, str(root))
    except Exception:
        return


def _parse_json_input(input_data: str) -> Dict[str, Any]:
    if not input_data:
        return {}
    text = input_data.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _count_diff_changes(diff_lines: list) -> Tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_lines:
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _filter_unified_hunks(diff_lines: list) -> list:
    keep_prefixes = ("@@", "+", "-", " ")
    return [line for line in diff_lines if line.startswith(keep_prefixes)]


def _build_apply_patch_from_hunks(path: str, hunk_lines: list) -> list:
    lines = [f"*** Update File: {path}"]
    lines.extend(hunk_lines)
    return lines


def _find_all_matches(lines: list, pattern: list) -> list:
    matches = []
    if not pattern:
        return matches
    for idx in range(0, len(lines) - len(pattern) + 1):
        if lines[idx:idx + len(pattern)] == pattern:
            matches.append(idx)
    return matches


def _normalize_match_line(line: str) -> str:
    return (line or "").strip()


def _find_all_matches_relaxed(lines: list, pattern: list) -> list:
    matches = []
    if not pattern:
        return matches
    normalized_lines = [_normalize_match_line(line) for line in lines]
    normalized_pattern = [_normalize_match_line(line) for line in pattern]
    for idx in range(0, len(lines) - len(pattern) + 1):
        if normalized_lines[idx:idx + len(pattern)] == normalized_pattern:
            matches.append(idx)
    return matches


def _parse_apply_patch(patch_text: str) -> list:
    text = _normalize_newlines(patch_text or "").strip("\n")
    if not text:
        raise ValueError("Patch content is empty.")
    lines = text.split("\n")
    if lines[0].strip() != "*** Begin Patch":
        raise ValueError("Patch must start with '*** Begin Patch'.")

    patches = []
    i = 1
    while i < len(lines):
        raw = lines[i].strip()
        if raw == "*** End Patch":
            if not patches:
                raise ValueError("Patch contains no file changes.")
            return patches
        if raw.startswith("*** Update File:"):
            path = raw.split(":", 1)[1].strip()
            if not path:
                raise ValueError("Missing file path in Update File.")
            i += 1
            hunks = []
            current = []
            saw_hunk_header = False
            while i < len(lines):
                line = lines[i]
                if line.startswith("*** "):
                    break
                if line.startswith("@@"):
                    saw_hunk_header = True
                    if current:
                        hunks.append(current)
                        current = []
                    i += 1
                    continue
                if not line:
                    raise ValueError("Patch lines must start with +, -, or space. Empty line found.")
                prefix = line[0]
                if prefix not in (" ", "+", "-"):
                    raise ValueError("Patch lines must start with +, -, or space.")
                current.append((prefix, line[1:]))
                i += 1
            if current:
                hunks.append(current)
            if not saw_hunk_header:
                raise ValueError("Missing @@ hunk header. Add more context with @@ lines.")
            if not hunks:
                raise ValueError("No hunk content found.")
            patches.append({
                "kind": "update",
                "path": path,
                "hunks": hunks
            })
            continue
        if raw.startswith("*** Add File:"):
            path = raw.split(":", 1)[1].strip()
            if not path:
                raise ValueError("Missing file path in Add File.")
            i += 1
            add_lines = []
            while i < len(lines):
                line = lines[i]
                if line.startswith("*** "):
                    break
                if not line:
                    raise ValueError("Add File lines must start with +.")
                if not line.startswith("+"):
                    raise ValueError("Add File lines must start with +.")
                add_lines.append(line[1:])
                i += 1
            patches.append({
                "kind": "add",
                "path": path,
                "lines": add_lines
            })
            continue
        if raw.startswith("*** Delete File:"):
            path = raw.split(":", 1)[1].strip()
            if not path:
                raise ValueError("Missing file path in Delete File.")
            i += 1
            while i < len(lines) and not lines[i].startswith("*** "):
                if lines[i].strip():
                    raise ValueError("Delete File patch should not include content lines.")
                i += 1
            patches.append({
                "kind": "delete",
                "path": path
            })
            continue
        raise ValueError(f"Unexpected patch line: {lines[i]}")

    raise ValueError("Patch must end with '*** End Patch'.")


def _apply_update_hunks(lines: list, hunks: list) -> list:
    updated = list(lines)
    for hunk in hunks:
        pattern = [text for prefix, text in hunk if prefix in (" ", "-")]
        if not pattern:
            raise ValueError("Hunk has no context. Add more surrounding lines.")
        matches = _find_all_matches(updated, pattern)
        if len(matches) == 0:
            matches = _find_all_matches_relaxed(updated, pattern)
            if len(matches) == 0:
                raise ValueError("Hunk context not found. Provide more context.")
            if len(matches) > 1:
                raise ValueError("Hunk context is not unique after whitespace relaxation. Provide more context.")
        elif len(matches) > 1:
            raise ValueError("Hunk context is not unique. Provide more context.")
        start = matches[0]
        matched_slice = updated[start:start + len(pattern)]
        new_segment = []
        pattern_idx = 0
        for prefix, text in hunk:
            if prefix == " ":
                new_segment.append(matched_slice[pattern_idx])
                pattern_idx += 1
            elif prefix == "-":
                pattern_idx += 1
            elif prefix == "+":
                new_segment.append(text)
        updated = updated[:start] + new_segment + updated[start + len(pattern):]
    return updated


def _apply_patch_text(patch_text: str) -> Dict[str, Any]:
    patches = _parse_apply_patch(patch_text)
    summary = []
    diffs = []
    revert_sections = ["*** Begin Patch"]

    for patch in patches:
        kind = patch["kind"]
        path = patch["path"]
        file_path = _resolve_path(path, "apply_patch", "write")
        if kind == "update":
            if not file_path.exists():
                raise ValueError(f"File not found: {path}")
            original = file_path.read_text(encoding="utf-8", errors="replace")
            original_norm = _normalize_newlines(original)
            old_lines = original_norm.split("\n")
            new_lines = _apply_update_hunks(old_lines, patch["hunks"])
            if new_lines == old_lines:
                raise ValueError("Patch did not change file contents.")
            new_text = "\n".join(new_lines)
            if original_norm.endswith("\n"):
                new_text += "\n"
            file_path.write_text(new_text, encoding="utf-8")

            diff_lines = list(difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm=""
            ))
            added, removed = _count_diff_changes(diff_lines)
            summary.append({"path": path, "added": added, "removed": removed})
            if diff_lines:
                diffs.append("\n".join(diff_lines))

            revert_diff = list(difflib.unified_diff(
                new_lines,
                old_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm=""
            ))
            revert_hunks = _filter_unified_hunks(revert_diff)
            revert_sections.extend(_build_apply_patch_from_hunks(path, revert_hunks))
        elif kind == "add":
            if file_path.exists():
                raise ValueError(f"File already exists: {path}")
            add_lines = patch.get("lines", [])
            new_text = "\n".join(add_lines)
            if add_lines:
                new_text += "\n"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new_text, encoding="utf-8")
            diff_lines = list(difflib.unified_diff(
                [],
                add_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm=""
            ))
            added, removed = _count_diff_changes(diff_lines)
            summary.append({"path": path, "added": added, "removed": removed})
            if diff_lines:
                diffs.append("\n".join(diff_lines))
            revert_sections.append(f"*** Delete File: {path}")
        elif kind == "delete":
            if not file_path.exists():
                raise ValueError(f"File not found: {path}")
            original = file_path.read_text(encoding="utf-8", errors="replace")
            original_norm = _normalize_newlines(original)
            old_lines = original_norm.split("\n")
            file_path.unlink()
            diff_lines = list(difflib.unified_diff(
                old_lines,
                [],
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm=""
            ))
            added, removed = _count_diff_changes(diff_lines)
            summary.append({"path": path, "added": added, "removed": removed})
            if diff_lines:
                diffs.append("\n".join(diff_lines))
            revert_sections.append(f"*** Add File: {path}")
            revert_sections.extend([f"+{line}" for line in old_lines])
        else:
            raise ValueError(f"Unsupported patch action: {kind}")

    revert_sections.append("*** End Patch")
    combined_diff = "\n\n".join([diff for diff in diffs if diff]).strip()
    revert_patch = "\n".join(revert_sections)

    return {
        "ok": True,
        "summary": summary,
        "diff": combined_diff,
        "revert_patch": revert_patch
    }


def _extract_command_name(command: str) -> str:
    if not command:
        return ""
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = command.strip().split()
    if not parts:
        return ""
    first = parts[0].strip().strip('"').strip("'")
    base = os.path.basename(first).lower()
    if base.endswith(".exe") or base.endswith(".cmd") or base.endswith(".bat"):
        base = os.path.splitext(base)[0]
    return base


def _contains_shell_operators(command: str) -> bool:
    for token in ("&", "|", ">", "<", ";"):
        if token in command:
            return True
    return False


def _join_command(parts: List[str]) -> str:
    if not parts:
        return ""
    if _is_windows():
        return subprocess.list2cmdline(parts)
    joiner = getattr(shlex, "join", None)
    if joiner:
        return joiner(parts)
    return " ".join(shlex.quote(part) for part in parts)


def _rewrite_rg_command(command: str, root: Path) -> str:
    if not command or _contains_shell_operators(command):
        return command
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        return command
    if not parts:
        return command
    head = parts[0].strip().strip('"').strip("'")
    base = os.path.basename(head).lower()
    if base.endswith(".exe") or base.endswith(".cmd") or base.endswith(".bat"):
        base = os.path.splitext(base)[0]
    if base != "rg":
        return command
    rg_exe = _resolve_rg_executable(root)
    if not rg_exe or rg_exe == "rg":
        return command
    parts[0] = rg_exe
    return _join_command(parts)


def _extract_path_candidates(command: str) -> list:
    try:
        parts = shlex.split(command, posix=False)
    except Exception:
        parts = command.strip().split()
    candidates: list = []
    for part in parts:
        item = part.strip().strip('"').strip("'")
        if not item:
            continue
        candidates.append(item)
        if "=" in item:
            _, value = item.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                candidates.append(value)
    return candidates


def _looks_like_path(candidate: str) -> bool:
    if not candidate:
        return False
    if candidate.startswith(("\\\\", "/")):
        return True
    if len(candidate) > 2 and candidate[1] == ":":
        return True
    return "\\" in candidate or "/" in candidate


def _command_targets_outside_root(command: str, root: Path) -> bool:
    if ".." in command:
        return True
    for candidate in _extract_path_candidates(command):
        if not _looks_like_path(candidate):
            continue
        try:
            path = Path(candidate)
        except Exception:
            continue
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        if not _is_within_root(path, root):
            return True
    return False


def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _pty_debug_enabled() -> bool:
    raw = os.environ.get("PTY_DEBUG")
    if raw is not None:
        value = str(raw).strip().lower()
        if value in ("0", "false", "no", "off"):
            return False
        if value in ("1", "true", "yes", "on"):
            return True
    dev_value = str(os.environ.get("TAURI_AGENT_DEV", "")).strip().lower()
    if dev_value in ("1", "true", "yes", "on"):
        return True
    env_value = str(os.environ.get("TAURI_AGENT_ENV", "")).strip().lower()
    return env_value in ("dev", "development")


def _pty_debug(message: str) -> None:
    if _pty_debug_enabled():
        print(f"[PTY DEBUG] {message}")


def _tail_bytes_hex(data: bytes, max_len: int = 16) -> str:
    if not data:
        return ""
    return data[-max_len:].hex()


def _use_restricted_conpty(use_sandbox: bool) -> bool:
    return bool(use_sandbox)


def _attach_pty_ws_emitter(pty_proc: PtyProcess) -> None:
    session_id = getattr(pty_proc, "session_id", "")
    if not session_id:
        return
    hub = get_ws_hub()

    def _emit(chunk: bytes, cursor: int) -> None:
        text = _decode_output_bytes(chunk)
        payload = {
            "type": "pty_output",
            "session_id": session_id,
            "pty_id": pty_proc.id,
            "chunk": text,
            "cursor": cursor,
            "status": pty_proc.status,
            "exit_code": pty_proc.exit_code
        }
        hub.emit_threadsafe(session_id, payload)

    pty_proc.set_on_output(_emit)


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
                kernel32.TerminateProcess(proc_info.hProcess, 1)
                kernel32.CloseHandle(proc_info.hThread)
                kernel32.CloseHandle(proc_info.hProcess)
                kernel32.CloseHandle(stdout_read)
                if job_handle:
                    kernel32.CloseHandle(job_handle)
                raise subprocess.TimeoutExpired(command, timeout_sec)
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


def _append_idle_timeout_info(output: str, elapsed_ms: Optional[int]) -> str:
    if not output or "[idle_timeout]" not in output:
        return output
    if elapsed_ms is None or elapsed_ms < 0:
        return output
    marker = f"[idle_timeout elapsed_ms={int(elapsed_ms)}]"
    if marker in output:
        return output
    return f"{output}\n{marker}"


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
        raise subprocess.TimeoutExpired(command, timeout_sec)
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
        terminator=_terminator
    )
    _attach_pty_ws_emitter(pty_proc)

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
        terminator=_terminator
    )
    _attach_pty_ws_emitter(pty_proc)

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
    cols: int = 120,
    rows: int = 30,
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
        raise subprocess.TimeoutExpired(command, timeout_sec)
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
        terminator=_terminator
    )
    _attach_pty_ws_emitter(pty_proc)

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
        terminator=_terminator
    )
    _attach_pty_ws_emitter(pty_proc)

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
        terminator=_terminator
    )
    _attach_pty_ws_emitter(pty_proc)

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


def _looks_like_permission_denied(message: str) -> bool:
    if not message:
        return False
    text = str(message)
    lower = text.lower()
    patterns = [
        "access is denied",
        "permission denied",
        "permissiondenied",
        "unauthorizedaccessexception",
        "not authorized",
        "accessdenied",
        "eacces",
        "eperm",
        "\u6743\u9650\u4e0d\u8db3",
        "\u62d2\u7edd\u8bbf\u95ee",
        "\u6ca1\u6709\u6743\u9650",
        "\u65e0\u6743\u9650"
    ]
    return any(pattern in lower for pattern in patterns)


async def _wait_for_permission(request_id: Optional[int], timeout_sec: float) -> str:
    if not request_id:
        return "denied"
    start = time.monotonic()
    while True:
        try:
            from database import db
            record = db.get_permission_request(request_id)
            if record and record.get("status") and record["status"] != "pending":
                return record["status"]
        except Exception:
            pass

        if timeout_sec is not None and (time.monotonic() - start) >= timeout_sec:
            try:
                from database import db
                db.update_permission_request(request_id, "timeout")
            except Exception:
                pass
            return "timeout"

        await asyncio.sleep(0.5)


class ReadFileTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "read_file"
        self.description = "Read a file inside the work path (line-based or smart block mode)."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Relative path under the work path.",
                required=True
            ),
            ToolParameter(
                name="mode",
                type="string",
                description="Read mode: lines or smart.",
                required=False,
                default="lines"
            ),
            ToolParameter(
                name="start_line",
                type="number",
                description="Start line (1-based) for lines mode.",
                required=False
            ),
            ToolParameter(
                name="line_count",
                type="number",
                description="Number of lines to read for lines mode.",
                required=False
            ),
            ToolParameter(
                name="anchor_line",
                type="number",
                description="Anchor line (1-based) for smart mode.",
                required=False
            ),
            ToolParameter(
                name="indent_level",
                type="number",
                description="Indent levels above anchor to include for smart mode (1 level = 4 spaces or 1 tab).",
                required=False
            ),
            ToolParameter(
                name="max_chars",
                type="number",
                description="Max characters to output.",
                required=False
            ),
            ToolParameter(
                name="encoding",
                type="string",
                description="Text encoding.",
                required=False,
                default="utf-8"
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        path = data.get("path") or input_data
        if not path:
            raise ValueError("Missing path.")
        mode = str(data.get("mode") or "").strip().lower()
        raw_start_line = data.get("start_line")
        if raw_start_line is None:
            raw_start_line = data.get("startLine")
        raw_line_count = data.get("line_count")
        if raw_line_count is None:
            raw_line_count = data.get("lineCount")
        raw_anchor_line = data.get("anchor_line")
        if raw_anchor_line is None:
            raw_anchor_line = data.get("anchorLine")

        has_start = raw_start_line is not None
        has_line_count = raw_line_count is not None
        has_anchor = raw_anchor_line is not None

        if not mode:
            if has_start or has_line_count:
                mode = "lines"
            elif has_anchor:
                mode = "smart"
            else:
                mode = "lines"
        if mode == "smart" and not has_anchor and (has_start or has_line_count):
            mode = "lines"
        start_line = int(raw_start_line if raw_start_line is not None else 1)
        line_count = int(raw_line_count) if raw_line_count is not None else 200
        anchor_line = int(raw_anchor_line) if raw_anchor_line is not None else None
        indent_level = data.get("indent_level")
        indent_level = int(indent_level) if indent_level is not None else 1
        files_cfg = get_tool_config().get("files", {})
        config_max_bytes = int(files_cfg.get("max_bytes", 20000))
        config_max_chars = int(files_cfg.get("max_chars", config_max_bytes))
        max_chars = data.get("max_chars")
        max_chars = int(max_chars) if max_chars is not None else config_max_chars
        if max_chars <= 0:
            raise ValueError("Invalid max_chars.")
        encoding = data.get("encoding") or "utf-8"

        file_path = _resolve_path(str(path), self.name, "read")
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")
        if config_max_bytes <= 0:
            raise ValueError("Invalid max_bytes configuration.")

        if mode not in ("lines", "smart"):
            raise ValueError("Invalid mode. Use 'lines' or 'smart'.")

        def _count_indent_width(line: str) -> int:
            width = 0
            for ch in line:
                if ch == " ":
                    width += 1
                elif ch == "\t":
                    width += 4
                else:
                    break
            return width

        def _is_comment_line(line: str) -> bool:
            stripped = line.lstrip()
            return stripped.startswith("//") or stripped.startswith("#")

        def _iter_lines():
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                for idx, raw_line in enumerate(f, start=1):
                    yield idx, raw_line.rstrip("\r\n")

        selected: List[Tuple[int, str]] = []
        if mode == "lines":
            if start_line <= 0:
                raise ValueError("start_line must be >= 1.")
            if line_count <= 0:
                raise ValueError("line_count must be positive.")
            end_line = start_line + line_count - 1
            for idx, line in _iter_lines():
                if idx < start_line:
                    continue
                if idx > end_line:
                    break
                selected.append((idx, line))
            if not selected:
                return "No content."
        else:
            if anchor_line is None or anchor_line <= 0:
                raise ValueError("anchor_line must be provided for smart mode.")
            if indent_level < 0:
                indent_level = 0

            lines: List[str] = []
            indent_widths: List[int] = []
            last_indent = 0
            anchor_idx = anchor_line - 1
            anchor_indent = None
            threshold = None
            start_idx = None
            end_idx = None
            anchor_reached = False

            for idx, line in _iter_lines():
                if not line.strip():
                    indent = last_indent
                else:
                    indent = _count_indent_width(line)
                indent_widths.append(indent)
                lines.append(line)
                last_indent = indent

                if not anchor_reached and idx == anchor_line:
                    anchor_reached = True
                    anchor_indent = indent
                    threshold = anchor_indent - (indent_level * 4)
                    if threshold < 0:
                        threshold = 0
                    start_idx = anchor_idx
                    i = anchor_idx - 1
                    while i >= 0:
                        prev_indent = indent_widths[i]
                        prev_line = lines[i]
                        is_comment = _is_comment_line(prev_line)
                        if prev_indent > threshold or is_comment:
                            start_idx = i
                            i -= 1
                            continue
                        break
                    end_idx = anchor_idx
                    continue

                if anchor_reached:
                    is_comment = _is_comment_line(line)
                    if indent > threshold or is_comment:
                        end_idx = idx - 1
                        continue
                    break

            if not anchor_reached:
                raise ValueError("anchor_line exceeds total lines.")
            if start_idx is None or end_idx is None:
                return "No content."

            for idx in range(start_idx, end_idx + 1):
                selected.append((idx + 1, lines[idx]))

        output_parts: List[str] = []
        total_chars = 0
        for line_no, line in selected:
            if not line.strip():
                continue
            formatted = f"{line_no}: {line}"
            prefix = "\n" if output_parts else ""
            chunk = f"{prefix}{formatted}"
            if total_chars + len(chunk) > max_chars:
                remaining = max_chars - total_chars
                if remaining <= 0:
                    break
                output_parts.append(chunk[:remaining])
                total_chars = max_chars
                break
            output_parts.append(chunk)
            total_chars += len(chunk)

        return "".join(output_parts) if output_parts else "No content."


class ListFilesTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "list_files"
        self.description = "List files in a directory using a tree-like display (depth-first)."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Directory path (absolute or relative to work path).",
                required=False
            ),
            ToolParameter(
                name="max_depth",
                type="number",
                description="Max depth to traverse (default 2, max 5).",
                required=False
            ),
            ToolParameter(
                name="max_entries",
                type="number",
                description="Max number of entries to output (default 100, max 500).",
                required=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        raw_text = (input_data or "").strip()
        data = _parse_json_input(input_data)

        path = data.get("path")
        if not path and raw_text and not data:
            path = raw_text

        max_depth = _coerce_int(data.get("max_depth"), 2)
        if max_depth <= 0:
            max_depth = 2
        if max_depth > 5:
            max_depth = 5

        max_entries = _coerce_int(data.get("max_entries"), 100)
        if max_entries <= 0:
            max_entries = 100
        if max_entries > 500:
            max_entries = 500

        if path:
            # List files without path restrictions by default.
            root = Path(str(path)).expanduser()
            if not root.is_absolute():
                root = _get_root_path() / root
            root = root.resolve()
        else:
            root = _get_root_path()

        if not root.exists():
            raise ValueError(f"Path not found: {root}")
        if root.is_file():
            raise ValueError(f"Path must be a directory: {root}")

        output_lines: List[str] = [f"Absolute path: {root}"]
        entries = 0
        truncated = False

        def format_prefix(depth: int) -> str:
            if depth <= 0:
                return ""
            return ("|  " * (depth - 1)) + "|-- "

        def list_dir(current_dir: Path, depth: int) -> None:
            nonlocal entries, truncated
            if truncated:
                return
            try:
                children = list(current_dir.iterdir())
            except Exception as exc:
                raise ValueError(f"Failed to list directory: {exc}")

            file_entries = []
            dir_entries = []
            for child in children:
                try:
                    is_symlink = child.is_symlink()
                except Exception:
                    is_symlink = False

                suffix = ""
                is_dir = False
                if is_symlink:
                    suffix = "@"
                else:
                    try:
                        is_dir = child.is_dir()
                    except Exception:
                        is_dir = False
                    if is_dir:
                        suffix = "/"

                entry = (child, suffix, is_dir)
                if is_dir:
                    dir_entries.append(entry)
                else:
                    file_entries.append(entry)

            file_entries.sort(key=lambda e: e[0].name.lower())
            dir_entries.sort(key=lambda e: e[0].name.lower())

            for child, suffix, _ in file_entries:
                if entries >= max_entries:
                    truncated = True
                    return
                prefix = format_prefix(depth)
                output_lines.append(f"{prefix}{child.name}{suffix}")
                entries += 1

            if depth == 0 and file_entries and dir_entries:
                output_lines.append("")

            for idx, (child, suffix, _) in enumerate(dir_entries):
                if entries >= max_entries:
                    truncated = True
                    return
                prefix = format_prefix(depth)
                output_lines.append(f"{prefix}{child.name}{suffix}")
                entries += 1

                if depth < max_depth:
                    list_dir(child, depth + 1)
                    if truncated:
                        return

                if depth == 0 and idx < len(dir_entries) - 1:
                    output_lines.append("")

        list_dir(root, 0)

        if truncated:
            output_lines.append(f"More than {max_entries} entries found")

        return "\n".join(output_lines)


class WriteFileTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "write_file"
        self.description = "Write content to a file inside the work path."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Relative path under the work path.",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Content to write.",
                required=True
            ),
            ToolParameter(
                name="mode",
                type="string",
                description="write or append.",
                required=False,
                default="write"
            ),
            ToolParameter(
                name="encoding",
                type="string",
                description="Text encoding.",
                required=False,
                default="utf-8"
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        path = data.get("path")
        content = data.get("content")
        if path is None or content is None:
            raise ValueError("Missing path or content.")
        mode = (data.get("mode") or "write").lower()
        encoding = data.get("encoding") or "utf-8"
        _maybe_create_snapshot()
        file_path = _resolve_path(str(path), self.name, "write")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if mode == "append" else "w"
        with open(file_path, file_mode, encoding=encoding) as f:
            f.write(str(content))
        return f"[write_file] wrote {len(str(content))} chars to {file_path}"


class RunShellTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "run_shell"
        self.description = (
            "Run a shell command within the work path. "
            "Supports mode=oneshot|persistent. "
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
                description="oneshot or persistent (default oneshot).",
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
                description="Stdin content to write (one-shot or send).",
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
                description="Idle timeout in milliseconds (default 120000 for oneshot, 0 for persistent).",
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
                # - Otherwise, fall back to oneshot when a command exists.
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
                if _pty_debug_enabled():
                    if isinstance(stdin_value, str):
                        endswith_lf = stdin_value.endswith("\n")
                        endswith_crlf = stdin_value.endswith("\r\n")
                        _pty_debug(
                            "run_shell send stdin "
                            f"type=str len={len(stdin_value)} "
                            f"endswith_lf={endswith_lf} endswith_crlf={endswith_crlf}"
                        )
                    else:
                        _pty_debug(f"run_shell send stdin type={type(stdin_value).__name__}")
                if _is_windows() and isinstance(stdin_value, str) and stdin_value:
                    stdin_value = _normalize_windows_stdin(stdin_value)
                    if _pty_debug_enabled():
                        endswith_crlf = stdin_value.endswith("\r\n")
                        _pty_debug(
                            "run_shell send normalized "
                            f"len={len(stdin_value)} endswith_crlf={endswith_crlf}"
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
                return f"[pty_id={proc.id} status={proc.status} bytes_written={written}]"
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
                        f"pty_id={proc.id} cursor={cursor} max_output={max_output} status={proc.status}"
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
                header = (
                    f"[pty_id={proc.id} status={proc.status} pty={str(proc.pty_enabled).lower()} "
                    f"cursor={new_cursor} reset={str(reset).lower()} idle_timeout={proc.idle_timeout_ms} "
                    f"buffer_size={proc.buffer_size}]"
                )
                if proc.status == "exited":
                    header = f"{header} exit_code={proc.exit_code}"
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
        mode = str(data.get("mode") or "oneshot").strip().lower()
        if mode_override:
            mode = mode_override
        if mode not in ("oneshot", "persistent"):
            mode = "oneshot"
        if command is None:
            if mode == "persistent":
                command = ""
            else:
                command = input_data
        if command is None:
            raise ValueError("Missing command.")

        cmd_name = _extract_command_name(command)
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
            if not shell_unrestricted and cmd_name not in allowset:
                reasons.append("Command not in allowlist.")
        if reasons:
            request_id = None
            try:
                from database import db
                request_id = db.create_permission_request(
                    tool_name=self.name,
                    action="execute",
                    path=str(command),
                    reason=" ".join(reasons),
                    session_id=tool_ctx.get("session_id")
                )
            except Exception:
                request_id = None

            timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, timeout_sec)
            if status not in ("approved", "approved_once"):
                if status == "denied":
                    return "Permission denied."
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if status == "approved" and agent_mode == "default" and not shell_unrestricted and cmd_name not in allowset:
                _ensure_shell_allowlist_entry(cmd_name)

        cwd = data.get("cwd")
        workdir = _resolve_path(cwd, self.name, "execute") if cwd else root

        timeout_sec = None
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
            timeout_sec = float(timeout_value) if timeout_value is not None else float(get_tool_config().get("shell", {}).get("timeout_sec", 30))
        idle_timeout_ms = _resolve_idle_timeout_ms(mode, data.get("idle_timeout"))
        buffer_size = _resolve_buffer_size(data.get("buffer_size"))
        max_output = data.get("max_output")
        max_output = int(max_output) if max_output is not None else int(get_tool_config().get("shell", {}).get("max_output", 20000))

        stdin_value = data.get("stdin")
        if _is_windows() and isinstance(stdin_value, str) and stdin_value:
            stdin_value = _normalize_windows_stdin(stdin_value)
        stdin_bytes = _encode_stdin(stdin_value)
        pty_requested = data.get("pty")
        pty_supported = _supports_pty()
        if pty_requested is None:
            if mode == "oneshot":
                pty_enabled = False
                pty_fallback = False
            else:
                pty_enabled = pty_supported
                pty_fallback = False
        else:
            pty_enabled = bool(pty_requested) and pty_supported
            pty_fallback = bool(pty_requested) and not pty_supported
        if _pty_debug_enabled():
            _pty_debug(
                "run_shell pty_resolve "
                f"mode={mode} pty_requested={pty_requested} "
                f"pty_supported={pty_supported} pty_enabled={pty_enabled}"
            )

        def _run_unsandboxed() -> Tuple[int, str]:
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                timeout=timeout_sec
            )
            stdout = completed.stdout or b""
            stderr = completed.stderr or b""
            output_text = _decode_output_bytes(stdout + stderr)
            return completed.returncode, output_text

        def _run_oneshot(use_sandbox: bool) -> Tuple[int, str]:
            if pty_enabled:
                if _is_windows():
                    return _run_windows_pty_oneshot(
                        command,
                        workdir,
                        timeout_sec,
                        idle_timeout_ms,
                        stdin_bytes,
                        use_restricted_token=_use_restricted_conpty(use_sandbox)
                    )
                return _run_posix_pty_oneshot(command, workdir, timeout_sec, idle_timeout_ms, stdin_bytes, use_sandbox)
            if use_sandbox:
                return _run_sandboxed_command(command, workdir, timeout_sec)
            return _run_unsandboxed()

        def _start_persistent(use_sandbox: bool) -> PtyProcess:
            _pty_debug(
                "run_shell start_persistent "
                f"use_sandbox={use_sandbox} pty_enabled={pty_enabled} pty_supported={pty_supported} "
                f"pty_fallback={pty_fallback} command={command}"
            )
            if _is_windows():
                if pty_enabled:
                    return _start_windows_pty_persistent(
                        command,
                        workdir,
                        stdin_bytes,
                        idle_timeout_ms,
                        buffer_size,
                        use_restricted_token=_use_restricted_conpty(use_sandbox)
                    )
                if use_sandbox:
                    return _start_windows_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size)
                return _start_windows_unrestricted_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size)
            if pty_enabled:
                return _start_posix_pty_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size, use_sandbox)
            return _start_posix_pipe_persistent(command, workdir, stdin_bytes, idle_timeout_ms, buffer_size, use_sandbox)

        ran_sandbox = False
        try:
            if mode == "persistent":
                if agent_mode == "super":
                    pty_proc = _start_persistent(use_sandbox=False)
                elif cmd_name in unrestricted_allowset:
                    pty_proc = _start_persistent(use_sandbox=False)
                else:
                    ran_sandbox = True
                    pty_proc = _start_persistent(use_sandbox=True)
                manager = get_pty_manager()
                manager.register(pty_proc)
                chunk, cursor, _ = pty_proc.read(None, max_output)
                _pty_debug(f"run_shell persistent started pty_id={pty_proc.id} initial_chunk_len={len(chunk or '')}")
                header = (
                    f"[pty_id={pty_proc.id} status={pty_proc.status} pty={str(pty_proc.pty_enabled).lower()} "
                    f"idle_timeout={pty_proc.idle_timeout_ms} buffer_size={pty_proc.buffer_size}]"
                )
                if pty_fallback:
                    header = f"{header} pty_fallback=true"
                return f"{header}\n{chunk or ''}"

            exec_start = time.monotonic()
            if agent_mode == "super":
                returncode, output = await asyncio.to_thread(_run_oneshot, False)
            elif cmd_name in unrestricted_allowset:
                returncode, output = await asyncio.to_thread(_run_oneshot, False)
            else:
                ran_sandbox = True
                returncode, output = await asyncio.to_thread(_run_oneshot, True)
            elapsed_ms = int((time.monotonic() - exec_start) * 1000)
            output = _append_idle_timeout_info(output, elapsed_ms)
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as exc:
            request_id = None
            try:
                from database import db
                request_id = db.create_permission_request(
                    tool_name=self.name,
                    action="execute_unrestricted",
                    path=str(command),
                    reason=f"Sandbox execution failed: {str(exc)}",
                    session_id=tool_ctx.get("session_id")
                )
            except Exception:
                request_id = None

            timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, timeout_sec)
            if status not in ("approved", "approved_once"):
                if status == "denied":
                    return "Permission denied."
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if status == "approved" and agent_mode == "default" and not shell_unrestricted and cmd_name not in unrestricted_allowset:
                _ensure_shell_unrestricted_allowlist_entry(cmd_name)

            if mode == "persistent":
                pty_proc = _start_persistent(use_sandbox=False)
                manager = get_pty_manager()
                manager.register(pty_proc)
                chunk, cursor, _ = pty_proc.read(None, max_output)
                header = (
                    f"[pty_id={pty_proc.id} status={pty_proc.status} pty={str(pty_proc.pty_enabled).lower()} "
                    f"idle_timeout={pty_proc.idle_timeout_ms} buffer_size={pty_proc.buffer_size}]"
                )
                if pty_fallback:
                    header = f"{header} pty_fallback=true"
                return f"{header}\n{chunk or ''}"

            exec_start = time.monotonic()
            returncode, output = await asyncio.to_thread(_run_oneshot, False)
            elapsed_ms = int((time.monotonic() - exec_start) * 1000)
            output = _append_idle_timeout_info(output, elapsed_ms)

        if ran_sandbox and returncode != 0 and _looks_like_permission_denied(output):
            request_id = None
            try:
                from database import db
                request_id = db.create_permission_request(
                    tool_name=self.name,
                    action="execute_unrestricted",
                    path=str(command),
                    reason="Sandbox permission denied. Allow unrestricted execution?",
                    session_id=tool_ctx.get("session_id")
                )
            except Exception:
                request_id = None

            timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, timeout_sec)
            if status not in ("approved", "approved_once"):
                if status == "denied":
                    return f"[exit_code={returncode}]\n{output}"
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if status == "approved" and agent_mode == "default" and not shell_unrestricted and cmd_name not in unrestricted_allowset:
                _ensure_shell_unrestricted_allowlist_entry(cmd_name)

            exec_start = time.monotonic()
            returncode, output = await asyncio.to_thread(_run_oneshot, False)
            elapsed_ms = int((time.monotonic() - exec_start) * 1000)
            output = _append_idle_timeout_info(output, elapsed_ms)

        output = output or "(no output)"
        output = _apply_max_output(output, max_output)
        header = f"[exit_code={returncode}]"
        if pty_fallback:
            header = f"{header} pty_fallback=true"
        return f"{header}\n{output}"


class RgTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "rg"
        self.description = "Search file contents using ripgrep (rg)."
        self.parameters = [
            ToolParameter(
                name="pattern",
                type="string",
                description="Search pattern (regular expression, rg syntax).",
                required=True
            ),
            ToolParameter(
                name="path",
                type="string",
                description="Search path (relative to work path).",
                required=False
            ),
            ToolParameter(
                name="glob",
                type="string",
                description="Optional glob filter (e.g. '*.ts').",
                required=False
            ),
            ToolParameter(
                name="case_sensitive",
                type="boolean",
                description="Case sensitive search.",
                required=False,
                default=False
            ),
            ToolParameter(
                name="max_results",
                type="number",
                description="Max number of output lines.",
                required=False,
                default=100
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        pattern = data.get("pattern") or input_data
        if not pattern:
            raise ValueError("Missing pattern.")

        path = data.get("path") or "."
        search_path = _resolve_path(str(path), self.name, "read")
        root = _get_root_path()
        case_sensitive = bool(data.get("case_sensitive", False))
        glob = data.get("glob")
        max_results = data.get("max_results")
        output_limit = int(max_results) if max_results is not None else 100
        if output_limit <= 0:
            output_limit = 100

        rg_exe = _resolve_rg_executable(root)
        base_args = [rg_exe, "--no-messages"]
        if not case_sensitive:
            base_args.append("-i")
        if glob:
            base_args.extend(["--glob", str(glob)])

        # 1) Collect matching files sorted by recent modification time.
        files_args = [
            *base_args,
            "--files-with-matches",
            "--sortr",
            "modified",
            "--regexp",
            str(pattern),
            "--",
            str(search_path),
        ]

        def _run(args: List[str]) -> Tuple[int, str]:
            completed = subprocess.run(
                args,
                cwd=str(root),
                capture_output=True,
                text=True
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return completed.returncode, output

        try:
            returncode, output = await asyncio.to_thread(_run, files_args)
        except FileNotFoundError:
            raise ValueError("rg is not available on this system.")

        if returncode == 2:
            raise ValueError(output.strip() or "rg failed.")
        if returncode == 1 and not output.strip():
            return "No matches."

        files = [line.strip() for line in output.splitlines() if line.strip()]
        if not files:
            return "No matches."

        # 2) For each matched file, return grouped results with relative path and line context.
        results: List[str] = []
        grouped: Dict[str, List[str]] = {}
        root_path = root.resolve()

        def _to_relative_path(raw_path: str) -> str:
            try:
                p = Path(raw_path)
                if not p.is_absolute():
                    return str(p)
                p = p.resolve()
                if _is_within_root(p, root_path):
                    return os.path.relpath(str(p), str(root_path))
            except Exception:
                return raw_path
            return raw_path

        def _format_match_text(text: str, max_len: int = 120) -> str:
            cleaned = " ".join(str(text).strip().split())
            if not cleaned:
                return "(empty)"
            if len(cleaned) <= max_len:
                return cleaned
            return f"{cleaned[:max_len - 3]}..."
        for file_path in files:
            remaining = output_limit - len(results)
            if remaining <= 0:
                break
            line_args = [
                *base_args,
                "--with-filename",
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--regexp",
                str(pattern),
            ]
            if remaining > 0:
                line_args.extend(["--max-count", str(remaining)])
            # Use "--" to prevent patterns starting with "-" from being parsed as flags.
            line_args.extend(["--", file_path])

            try:
                line_code, line_output = await asyncio.to_thread(_run, line_args)
            except FileNotFoundError:
                raise ValueError("rg is not available on this system.")

            if line_code == 2:
                raise ValueError(line_output.strip() or "rg failed.")
            if line_code == 1 and not line_output.strip():
                continue

            for raw in line_output.splitlines():
                if not raw.strip():
                    continue
                parts = raw.rsplit(":", 2)
                if len(parts) < 2:
                    continue
                path_part = _to_relative_path(parts[0])
                line_no = parts[1]
                line_text = parts[2] if len(parts) > 2 else ""
                entry = f"  {_format_match_text(line_text)}:L{line_no}"
                grouped.setdefault(path_part, []).append(entry)
                results.append(path_part)
                if len(results) >= output_limit:
                    break

        if not grouped:
            return "No matches."

        output_lines: List[str] = []
        for file_path in files:
            rel_path = _to_relative_path(file_path)
            items = grouped.get(rel_path)
            if not items:
                continue
            output_lines.append(rel_path)
            output_lines.extend(items)
        return "\n".join(output_lines)


class ApplyPatchTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "apply_patch"
        self.description = (
            "Apply a patch to files. Format:\n"
            "*** Begin Patch\n"
            "*** Update File: path\n"
            "@@\n"
            "- old line\n"
            "+ new line\n"
            "*** End Patch"
        )
        self.parameters = [
            ToolParameter(
                name="patch",
                type="string",
                description="Patch content in apply_patch format.",
                required=True
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        patch_text = data.get("patch") or input_data
        if not patch_text:
            raise ValueError("Missing patch content.")
        try:
            _maybe_create_snapshot()
            result = _apply_patch_text(patch_text)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


class TavilySearchTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "search"
        self.description = "Search the web using Tavily."
        self.parameters = [
            ToolParameter(
                name="query",
                type="string",
                description="Search query.",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type="number",
                description="Max results.",
                required=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        query = data.get("query") or input_data
        if not query:
            raise ValueError("Missing query.")

        search_cfg = get_tool_config().get("search", {})
        api_key = search_cfg.get("tavily_api_key") or ""
        if not api_key:
            return "Tavily API key not configured. Set TAVILY_API_KEY or tools_config.json."

        max_results = data.get("max_results")
        max_results = int(max_results) if max_results is not None else int(search_cfg.get("max_results", 5))
        search_depth = search_cfg.get("search_depth", "basic")
        min_score = search_cfg.get("min_score", 0.4)
        try:
            min_score = float(min_score)
        except (TypeError, ValueError):
            min_score = 0.4

        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()

        results = data.get("results", []) if isinstance(data, dict) else []
        filtered_results = []
        for item in results:
            score = item.get("score") if isinstance(item, dict) else None
            if score is not None:
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    score = None
            if score is not None and score < min_score:
                continue
            filtered_results.append(item)
        if not filtered_results:
            return "No results."

        lines = ["Search results:"]
        for idx, item in enumerate(filtered_results, start=1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("content", "")
            lines.append(f"{idx}. {title}")
            if url:
                lines.append(url)
            if snippet:
                lines.append(snippet)
            lines.append("")

        return "\n".join(lines).strip()


_AST_EXT_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".c": "c",
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".inl": "cpp",
    ".rs": "rust",
    ".json": "json"
}

_AST_LANGUAGE_ALIASES = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "tsx": "tsx",
    "jsx": "javascript",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "rs": "rust",
    "rust": "rust",
    "json": "json"
}

_AST_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".venv",
    "venv"
}

_TS_PARSERS: Dict[str, Any] = {}


def _normalize_ast_language(language: Optional[str], path: Optional[Path]) -> Optional[str]:
    if language:
        key = str(language).strip().lower()
        if key in _AST_LANGUAGE_ALIASES:
            return _AST_LANGUAGE_ALIASES[key]
    if path:
        return _AST_EXT_LANGUAGE.get(path.suffix.lower())
    return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def _get_ast_config() -> Dict[str, Any]:
    cfg = get_tool_config().get("ast", {})
    return {
        "max_bytes": _coerce_int(cfg.get("max_bytes", 200000), 200000),
        "max_nodes": _coerce_int(cfg.get("max_nodes", 2000), 2000),
        "max_depth": _coerce_int(cfg.get("max_depth", 12), 12),
        "max_files": _coerce_int(cfg.get("max_files", 500), 500),
        "max_symbols": _coerce_int(cfg.get("max_symbols", 2000), 2000),
        "include_text": _coerce_bool(cfg.get("include_text", False), False)
    }


def _read_file_bytes(file_path: Path, max_bytes: int) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        size = file_path.stat().st_size
    except Exception as exc:
        return None, f"Failed to stat file: {exc}"
    if max_bytes and size > max_bytes:
        return None, f"File too large ({size} bytes). Max allowed is {max_bytes}."
    try:
        return file_path.read_bytes(), None
    except Exception as exc:
        return None, f"Failed to read file: {exc}"


def _format_position(start_line: int, start_col: int, end_line: int, end_col: int) -> Dict[str, Any]:
    return {
        "start": [start_line, start_col],
        "end": [end_line, end_col]
    }


def _py_node_position(node: py_ast.AST) -> Optional[Dict[str, Any]]:
    if not hasattr(node, "lineno"):
        return None
    start_line = int(getattr(node, "lineno", 0) or 0)
    start_col = int(getattr(node, "col_offset", 0) or 0) + 1
    end_line = int(getattr(node, "end_lineno", start_line) or start_line)
    end_col = int(getattr(node, "end_col_offset", 0) or 0) + 1
    if start_line <= 0:
        return None
    return _format_position(start_line, start_col, end_line, end_col)


def _format_py_args(args: py_ast.arguments) -> str:
    parts: List[str] = []
    for arg in getattr(args, "posonlyargs", []) or []:
        parts.append(arg.arg)
    if getattr(args, "posonlyargs", None):
        parts.append("/")
    for arg in getattr(args, "args", []) or []:
        parts.append(arg.arg)
    vararg = getattr(args, "vararg", None)
    if vararg is not None:
        parts.append("*" + vararg.arg)
    elif getattr(args, "kwonlyargs", None):
        parts.append("*")
    for arg in getattr(args, "kwonlyargs", []) or []:
        parts.append(arg.arg)
    kwarg = getattr(args, "kwarg", None)
    if kwarg is not None:
        parts.append("**" + kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _truncate_text(value: str, max_len: int = 120) -> str:
    text = (value or "").strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _py_outline(tree: py_ast.AST, include_positions: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    symbols: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []

    def add_symbol(kind: str, name: str, node: py_ast.AST, parent: Optional[str], signature: Optional[str] = None, bases: Optional[List[str]] = None):
        item: Dict[str, Any] = {"kind": kind, "name": name}
        if parent:
            item["parent"] = parent
        if signature:
            item["signature"] = signature
        if bases:
            item["bases"] = bases
        if include_positions:
            pos = _py_node_position(node)
            if pos:
                item.update(pos)
        symbols.append(item)

    def visit(node: py_ast.AST, class_name: Optional[str], parent_name: Optional[str]):
        for child in py_ast.iter_child_nodes(node):
            if isinstance(child, py_ast.Import):
                for alias in child.names:
                    entry = {"module": alias.name}
                    if alias.asname:
                        entry["as"] = alias.asname
                    if include_positions:
                        pos = _py_node_position(child)
                        if pos:
                            entry.update(pos)
                    imports.append(entry)
                continue
            if isinstance(child, py_ast.ImportFrom):
                module = child.module or ""
                names = [alias.name for alias in child.names]
                entry = {"module": module, "names": names}
                if child.level:
                    entry["level"] = child.level
                if include_positions:
                    pos = _py_node_position(child)
                    if pos:
                        entry.update(pos)
                imports.append(entry)
                continue
            if isinstance(child, py_ast.ClassDef):
                bases: List[str] = []
                for base in child.bases:
                    try:
                        bases.append(py_ast.unparse(base))
                    except Exception:
                        bases.append(base.__class__.__name__)
                add_symbol("class", child.name, child, parent_name, bases=bases if bases else None)
                visit(child, child.name, child.name)
                continue
            if isinstance(child, (py_ast.FunctionDef, py_ast.AsyncFunctionDef)):
                is_method = class_name is not None and parent_name == class_name
                kind = "async_method" if is_method and isinstance(child, py_ast.AsyncFunctionDef) else \
                    "method" if is_method else \
                    "async_function" if isinstance(child, py_ast.AsyncFunctionDef) else "function"
                signature = _format_py_args(child.args)
                parent = class_name if is_method else parent_name
                add_symbol(kind, child.name, child, parent, signature=signature)
                visit(child, class_name, child.name)
                continue
            visit(child, class_name, parent_name)

    visit(tree, None, None)
    return symbols, imports


def _py_full_tree(
    node: py_ast.AST,
    include_positions: bool,
    max_depth: int,
    max_nodes: int,
    state: Dict[str, Any],
    depth: int = 0
) -> Dict[str, Any]:
    if state["count"] >= max_nodes:
        state["truncated"] = True
        return {"type": node.__class__.__name__}
    state["count"] += 1

    data: Dict[str, Any] = {"type": node.__class__.__name__}
    if include_positions:
        pos = _py_node_position(node)
        if pos:
            data.update(pos)

    if isinstance(node, (py_ast.FunctionDef, py_ast.AsyncFunctionDef, py_ast.ClassDef)):
        data["name"] = node.name
    elif isinstance(node, py_ast.Name):
        data["name"] = node.id
    elif isinstance(node, py_ast.Attribute):
        data["attr"] = node.attr
    elif isinstance(node, py_ast.Constant):
        data["value"] = _truncate_text(repr(node.value), 80)

    if max_depth >= 0 and depth >= max_depth:
        return data

    children: List[Dict[str, Any]] = []
    for child in py_ast.iter_child_nodes(node):
        children.append(_py_full_tree(child, include_positions, max_depth, max_nodes, state, depth + 1))
        if state["count"] >= max_nodes:
            break
    if children:
        data["children"] = children
    return data


def _get_tree_sitter_parser(language: str) -> Tuple[Optional[Any], Optional[str]]:
    if language in _TS_PARSERS:
        return _TS_PARSERS[language], None
    try:
        from tree_sitter_languages import get_parser
    except Exception as exc:
        return None, f"tree_sitter_languages not available: {exc}"
    try:
        parser = get_parser(language)
    except Exception as exc:
        return None, f"Unsupported language '{language}': {exc}"
    _TS_PARSERS[language] = parser
    return parser, None


def _ts_node_text(source: bytes, node: Any, max_len: int = 120) -> str:
    try:
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""
    return _truncate_text(text, max_len)


def _ts_node_position(node: Any) -> Dict[str, Any]:
    start_line = int(node.start_point[0]) + 1
    start_col = int(node.start_point[1]) + 1
    end_line = int(node.end_point[0]) + 1
    end_col = int(node.end_point[1]) + 1
    return _format_position(start_line, start_col, end_line, end_col)


def _ts_get_name(node: Any, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        name_node = node.child_by_field_name("property")
    if name_node is None:
        return ""
    return _ts_node_text(source, name_node, 120)


def _ts_get_parameters(node: Any, source: bytes) -> Optional[str]:
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return None
    return _ts_node_text(source, params_node, 120)

_TS_IDENTIFIER_TYPES = {
    "qualified_identifier",
    "scoped_identifier",
    "identifier",
    "field_identifier",
    "type_identifier",
    "namespace_identifier",
    "operator_name",
    "destructor_name"
}

_C_LIKE_CLASS_TYPES = ("class_specifier", "struct_specifier", "union_specifier")


def _ts_find_first(node: Any, types: Set[str]) -> Optional[Any]:
    if node is None:
        return None
    if node.type in types:
        return node
    for child in node.named_children:
        found = _ts_find_first(child, types)
        if found is not None:
            return found
    return None


def _ts_find_identifier_node(node: Any) -> Optional[Any]:
    if node is None:
        return None
    if node.type in _TS_IDENTIFIER_TYPES:
        return node
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return name_node
    for child in node.named_children:
        found = _ts_find_identifier_node(child)
        if found is not None:
            return found
    return None


def _ts_c_like_name(node: Any, source: bytes) -> str:
    if node is None:
        return ""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _ts_node_text(source, name_node, 120)
    decl_node = node.child_by_field_name("declarator")
    if decl_node is not None:
        name = _ts_c_like_name(decl_node, source)
        if name:
            return name
    id_node = _ts_find_identifier_node(node)
    if id_node is None:
        return ""
    return _ts_node_text(source, id_node, 120)


def _ts_c_like_parameters(node: Any, source: bytes) -> Optional[str]:
    if node is None:
        return None
    params = _ts_get_parameters(node, source)
    if params:
        return params
    params_node = _ts_find_first(node, {"parameter_list"})
    if params_node is None:
        return None
    return _ts_node_text(source, params_node, 120)


def _ts_c_like_collect_declarators(node: Any) -> List[Any]:
    if node is None:
        return []
    declarators: List[Any] = []
    field_decl = node.child_by_field_name("declarator")
    if field_decl is not None:
        declarators.append(field_decl)
    for child in node.named_children:
        if child.type in ("init_declarator", "declarator", "field_declarator", "function_declarator"):
            declarators.append(child)
    deduped: List[Any] = []
    seen: Set[int] = set()
    for item in declarators:
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        deduped.append(item)
    return deduped


def _ts_outline(tree: Any, source: bytes, language: str, include_positions: bool, max_symbols: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    symbols: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []
    lang = (language or "").lower()
    c_like = lang in ("c", "cpp")

    def add_symbol(item: Dict[str, Any]):
        if len(symbols) >= max_symbols:
            return
        symbols.append(item)

    def walk(node: Any, class_stack: List[str]):
        if len(symbols) >= max_symbols:
            return
        node_type = node.type

        if node_type == "import_statement":
            entry = {"text": _ts_node_text(source, node, 200)}
            if include_positions:
                entry.update(_ts_node_position(node))
            imports.append(entry)
        elif c_like and node_type in ("preproc_include", "preproc_import"):
            entry = {"text": _ts_node_text(source, node, 200)}
            if include_positions:
                entry.update(_ts_node_position(node))
            imports.append(entry)
        elif node_type == "class_declaration":
            name = _ts_get_name(node, source)
            item = {"kind": "class", "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
            class_stack.append(name or "(anonymous)")
        elif c_like and node_type in _C_LIKE_CLASS_TYPES:
            name = _ts_get_name(node, source) or _ts_c_like_name(node, source)
            kind = "class" if node_type == "class_specifier" else \
                "struct" if node_type == "struct_specifier" else "union"
            item = {"kind": kind, "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
            class_stack.append(name or "(anonymous)")
        elif node_type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            name = _ts_get_name(node, source)
            kind = "interface" if node_type == "interface_declaration" else \
                "type_alias" if node_type == "type_alias_declaration" else "enum"
            item = {"kind": kind, "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type == "enum_specifier":
            name = _ts_get_name(node, source) or _ts_c_like_name(node, source)
            item = {"kind": "enum", "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type == "namespace_definition":
            name = _ts_get_name(node, source) or _ts_c_like_name(node, source)
            item = {"kind": "namespace", "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type == "type_definition":
            name = _ts_c_like_name(node, source)
            item = {"kind": "typedef", "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type == "alias_declaration":
            name = _ts_get_name(node, source) or _ts_c_like_name(node, source)
            item = {"kind": "type_alias", "name": name or "(anonymous)"}
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif node_type == "function_declaration":
            name = _ts_get_name(node, source)
            item = {"kind": "function", "name": name or "(anonymous)"}
            params = _ts_get_parameters(node, source)
            if params:
                item["signature"] = params
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif node_type == "method_definition":
            name = _ts_get_name(node, source)
            item = {"kind": "method", "name": name or "(anonymous)"}
            if class_stack:
                item["parent"] = class_stack[-1]
            params = _ts_get_parameters(node, source)
            if params:
                item["signature"] = params
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type == "function_definition":
            name = _ts_c_like_name(node, source)
            kind = "method" if class_stack else "function"
            item = {"kind": kind, "name": name or "(anonymous)"}
            if class_stack:
                item["parent"] = class_stack[-1]
            decl_node = node.child_by_field_name("declarator")
            if decl_node is not None:
                func_decl = _ts_find_first(decl_node, {"function_declarator"}) or decl_node
                params = _ts_c_like_parameters(func_decl, source)
            else:
                params = _ts_c_like_parameters(node, source)
            if params:
                item["signature"] = params
            if include_positions:
                item.update(_ts_node_position(node))
            add_symbol(item)
        elif c_like and node_type in ("declaration", "field_declaration"):
            for decl in _ts_c_like_collect_declarators(node):
                if len(symbols) >= max_symbols:
                    break
                func_decl = _ts_find_first(decl, {"function_declarator"})
                if func_decl is None and decl.type != "function_declarator":
                    continue
                name = _ts_c_like_name(decl, source)
                if not name:
                    continue
                kind = "method" if class_stack else "function"
                item = {"kind": kind, "name": name}
                if class_stack:
                    item["parent"] = class_stack[-1]
                params = _ts_c_like_parameters(func_decl or decl, source)
                if params:
                    item["signature"] = params
                if include_positions:
                    item.update(_ts_node_position(node))
                add_symbol(item)
        elif node_type == "variable_declarator":
            value_node = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if value_node is not None and value_node.type in ("arrow_function", "function", "function_expression"):
                name = _ts_node_text(source, name_node, 120) if name_node is not None else "(anonymous)"
                item = {"kind": "function", "name": name}
                params = _ts_get_parameters(value_node, source)
                if params:
                    item["signature"] = params
                if include_positions:
                    item.update(_ts_node_position(node))
                add_symbol(item)

        for child in node.named_children:
            walk(child, class_stack)

        if node_type == "class_declaration" and class_stack:
            class_stack.pop()
        elif c_like and node_type in _C_LIKE_CLASS_TYPES and class_stack:
            class_stack.pop()

    walk(tree.root_node, [])
    return symbols, imports


def _ts_full_tree(
    node: Any,
    source: bytes,
    include_positions: bool,
    include_text: bool,
    max_depth: int,
    max_nodes: int,
    state: Dict[str, Any],
    depth: int = 0
) -> Dict[str, Any]:
    if state["count"] >= max_nodes:
        state["truncated"] = True
        return {"type": node.type}
    state["count"] += 1

    data: Dict[str, Any] = {"type": node.type}
    if include_positions:
        data.update(_ts_node_position(node))
    if include_text and len(node.named_children) == 0:
        data["text"] = _ts_node_text(source, node, 120)

    if max_depth >= 0 and depth >= max_depth:
        return data

    children: List[Dict[str, Any]] = []
    for child in node.named_children:
        children.append(_ts_full_tree(child, source, include_positions, include_text, max_depth, max_nodes, state, depth + 1))
        if state["count"] >= max_nodes:
            break
    if children:
        data["children"] = children
    return data


def _collect_ast_files(
    root: Path,
    extensions: Optional[List[str]],
    ignore_dirs: Set[str],
    max_files: int,
    settings_root: Optional[Path] = None,
    settings: Optional[Dict[str, Any]] = None
) -> List[Path]:
    exts = extensions or list(_AST_EXT_LANGUAGE.keys())
    return collect_ast_files(
        scan_root=root,
        settings_root=settings_root or root,
        extensions=exts,
        max_files=max_files,
        ignore_dir_names=ignore_dirs,
        settings=settings
    )


class CodeAstTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "code_ast"
        self.description = "Parse code into AST or outline for a file or directory."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="File or directory path under the work path.",
                required=True
            ),
            ToolParameter(
                name="mode",
                type="string",
                description="outline (default) or full.",
                required=False,
                default="outline"
            ),
            ToolParameter(
                name="language",
                type="string",
                description="Optional language override (python, typescript, tsx, javascript, rust, json, c, cpp).",
                required=False
            ),
            ToolParameter(
                name="extensions",
                type="array",
                description="Optional file extensions filter for directory mode (e.g. ['.py', '.ts']).",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="max_files",
                type="number",
                description="Max files to scan in directory mode.",
                required=False
            ),
            ToolParameter(
                name="max_symbols",
                type="number",
                description="Max symbols to return (directory mode).",
                required=False
            ),
            ToolParameter(
                name="max_nodes",
                type="number",
                description="Max AST nodes to return (full mode).",
                required=False
            ),
            ToolParameter(
                name="max_depth",
                type="number",
                description="Max AST depth to return (full mode).",
                required=False
            ),
            ToolParameter(
                name="max_bytes",
                type="number",
                description="Max bytes per file.",
                required=False
            ),
            ToolParameter(
                name="include_positions",
                type="boolean",
                description="Include line/column positions.",
                required=False,
                default=True
            ),
            ToolParameter(
                name="include_text",
                type="boolean",
                description="Include text for leaf nodes (full mode).",
                required=False,
                default=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        path = data.get("path") or input_data
        if not path:
            raise ValueError("Missing path.")
        app_cfg = get_app_config()
        agent_cfg = app_cfg.get("agent", {}) if isinstance(app_cfg, dict) else {}
        if not agent_cfg.get("ast_enabled", True):
            return json.dumps({"ok": False, "path": path, "error": "AST disabled."})

        cfg = _get_ast_config()
        mode = str(data.get("mode") or "outline").strip().lower()
        language = data.get("language")
        extensions = data.get("extensions")
        max_symbols = _coerce_int(data.get("max_symbols"), cfg["max_symbols"])
        max_nodes = _coerce_int(data.get("max_nodes"), cfg["max_nodes"])
        max_depth = _coerce_int(data.get("max_depth"), cfg["max_depth"])
        max_bytes = _coerce_int(data.get("max_bytes"), cfg["max_bytes"])
        if max_symbols <= 0:
            max_symbols = cfg["max_symbols"]
        if max_nodes <= 0:
            max_nodes = cfg["max_nodes"]
        if max_depth < 0:
            max_depth = cfg["max_depth"]
        if max_bytes <= 0:
            max_bytes = cfg["max_bytes"]
        include_positions = _coerce_bool(data.get("include_positions"), True)
        include_text = _coerce_bool(data.get("include_text"), cfg["include_text"])

        target_path = _resolve_path(str(path), self.name, "read")
        if not target_path.exists():
            raise ValueError(f"Path not found: {target_path}")

        settings_root = _get_root_path()
        for root in _get_allowed_roots():
            if _is_within_root(target_path, root):
                settings_root = root
                break
        settings = get_ast_settings(str(settings_root))
        settings_max_files = settings.get("max_files") if isinstance(settings, dict) else None
        if settings_max_files is None:
            settings_max_files = cfg["max_files"]
        max_files = _coerce_int(data.get("max_files"), settings_max_files)
        if max_files <= 0:
            max_files = settings_max_files

        if mode not in ("outline", "full"):
            raise ValueError("Invalid mode. Use 'outline' or 'full'.")

        if target_path.is_dir():
            ignore_dirs = set(_AST_IGNORE_DIRS)
            collected_files = _collect_ast_files(
                target_path,
                extensions,
                ignore_dirs,
                max_files,
                settings_root=settings_root,
                settings=settings
            )
            results: List[Dict[str, Any]] = []
            total_symbols = 0
            truncated = False
            for file_path in collected_files:
                remaining_symbols = max_symbols - total_symbols
                if remaining_symbols <= 0:
                    truncated = True
                    break
                file_result = _ast_for_file(
                    file_path=file_path,
                    mode=mode,
                    language=language,
                    max_nodes=max_nodes,
                    max_depth=max_depth,
                    max_bytes=max_bytes,
                    include_positions=include_positions,
                    include_text=include_text,
                    max_symbols=remaining_symbols
                )
                if file_result.get("symbols"):
                    total_symbols += len(file_result.get("symbols") or [])
                results.append(file_result)
                if file_result.get("truncated"):
                    truncated = True
            payload = {
                "ok": True,
                "path": str(target_path),
                "mode": mode,
                "files": results,
                "truncated": truncated
            }
            return json.dumps(payload, ensure_ascii=False)

        result = _ast_for_file(
            file_path=target_path,
            mode=mode,
            language=language,
            max_nodes=max_nodes,
            max_depth=max_depth,
            max_bytes=max_bytes,
            include_positions=include_positions,
            include_text=include_text,
            max_symbols=max_symbols
        )
        return json.dumps(result, ensure_ascii=False)


def _ast_for_file(
    file_path: Path,
    mode: str,
    language: Optional[str],
    max_nodes: int,
    max_depth: int,
    max_bytes: int,
    include_positions: bool,
    include_text: bool,
    max_symbols: int
) -> Dict[str, Any]:
    raw, err = _read_file_bytes(file_path, max_bytes)
    if err:
        return {"ok": False, "path": str(file_path), "error": err}
    if raw is None:
        return {"ok": False, "path": str(file_path), "error": "Empty file."}

    lang = _normalize_ast_language(language, file_path)
    if not lang:
        return {"ok": False, "path": str(file_path), "error": "Unsupported file type."}

    try:
        if lang == "python":
            tree = py_ast.parse(raw)
            if mode == "outline":
                symbols, imports = _py_outline(tree, include_positions)
                if max_symbols and len(symbols) > max_symbols:
                    symbols = symbols[:max_symbols]
                return {
                    "ok": True,
                    "path": str(file_path),
                    "language": lang,
                    "mode": mode,
                    "symbols": symbols,
                    "imports": imports,
                    "truncated": len(symbols) >= max_symbols > 0
                }

            state = {"count": 0, "truncated": False}
            ast_tree = _py_full_tree(tree, include_positions, max_depth, max_nodes, state)
            return {
                "ok": True,
                "path": str(file_path),
                "language": lang,
                "mode": mode,
                "ast": ast_tree,
                "truncated": state["truncated"]
            }

        parser, parse_err = _get_tree_sitter_parser(lang)
        if parse_err:
            return {"ok": False, "path": str(file_path), "language": lang, "error": parse_err}
        tree = parser.parse(raw)

        if mode == "outline":
            symbols, imports = _ts_outline(tree, raw, lang, include_positions, max_symbols)
            return {
                "ok": True,
                "path": str(file_path),
                "language": lang,
                "mode": mode,
                "symbols": symbols,
                "imports": imports,
                "truncated": len(symbols) >= max_symbols > 0
            }

        state = {"count": 0, "truncated": False}
        ast_tree = _ts_full_tree(tree.root_node, raw, include_positions, include_text, max_depth, max_nodes, state)
        return {
            "ok": True,
            "path": str(file_path),
            "language": lang,
            "mode": mode,
            "ast": ast_tree,
            "truncated": state["truncated"]
        }
    except Exception as exc:
        return {"ok": False, "path": str(file_path), "language": lang, "error": str(exc)}
