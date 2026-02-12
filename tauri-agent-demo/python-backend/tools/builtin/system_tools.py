import asyncio
import ast as py_ast
import difflib
import json
import os
import shlex
import subprocess
import sys
import locale
import time
from pathlib import Path
from typing import Any, Dict, Tuple, Optional, List, Set

import httpx

from ..base import Tool, ToolParameter
from ..config import get_tool_config, update_tool_config
from ..context import get_tool_context
from app_config import get_app_config
from ast_settings import get_ast_settings
from ast_file_filter import collect_ast_files


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


def _escape_seatbelt_path(path: Path) -> str:
    raw = str(path)
    return raw.replace("\\", "\\\\").replace('"', '\\"')


def _build_macos_sandbox_profile(roots: List[Path]) -> str:
    allowed_paths = [_escape_seatbelt_path(root) for root in roots if root]
    allow_blocks = [
        '(allow file-read* (subpath "/usr") (subpath "/bin") (subpath "/System") (subpath "/Library"))',
        '(allow file-read* file-write* (subpath "/tmp") (subpath "/private/tmp") (subpath "/var/tmp"))',
        "(allow network*)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow process-signal)",
        "(allow sysctl-read)"
    ]
    if allowed_paths:
        roots_clause = " ".join([f'(subpath "{path}")' for path in allowed_paths])
        allow_blocks.append(f"(allow file-read* file-write* {roots_clause})")
    profile_lines = ["(version 1)", "(deny default)"]
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

    sid = wintypes.LPVOID()
    if advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):
        tml = TOKEN_MANDATORY_LABEL()
        tml.Label.Sid = sid
        tml.Label.Attributes = SE_GROUP_INTEGRITY
        if not advapi32.SetTokenInformation(
            restricted_token,
            TokenIntegrityLevel,
            ctypes.byref(tml),
            ctypes.sizeof(tml)
        ):
            print(f"[Shell Sandbox] SetTokenInformation failed (winerr={ctypes.get_last_error()})")
        kernel32.LocalFree(sid)

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
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return data.decode(encoding, errors="replace")

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
        self.description = "Read a file inside the work path."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Relative path under the work path.",
                required=True
            ),
            ToolParameter(
                name="start",
                type="number",
                description="Byte offset to start reading.",
                required=False,
                default=0
            ),
            ToolParameter(
                name="max_bytes",
                type="number",
                description="Max bytes to read.",
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
        start = int(data.get("start", 0) or 0)
        max_bytes = data.get("max_bytes")
        config_max = int(get_tool_config().get("files", {}).get("max_bytes", 20000))
        max_bytes = int(max_bytes) if max_bytes is not None else config_max
        encoding = data.get("encoding") or "utf-8"

        file_path = _resolve_path(str(path), self.name, "read")
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")
        if start < 0 or max_bytes <= 0:
            raise ValueError("Invalid start or max_bytes.")

        with open(file_path, "rb") as f:
            f.seek(start)
            raw = f.read(max_bytes)

        text = raw.decode(encoding, errors="replace")
        header = f"[read_file] {file_path} bytes={len(raw)} offset={start}"
        return f"{header}\n{text}"


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
        self.description = "Run a shell command within the work path."
        self.parameters = [
            ToolParameter(
                name="command",
                type="string",
                description="Shell command to run.",
                required=True
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
                name="max_output",
                type="number",
                description="Max output characters.",
                required=False
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        command = data.get("command") or input_data
        if not command:
            raise ValueError("Missing command.")
        cmd_name = _extract_command_name(command)
        root = _get_root_path()
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

        timeout = data.get("timeout_sec")
        timeout_sec = float(timeout) if timeout is not None else float(get_tool_config().get("shell", {}).get("timeout_sec", 30))
        max_output = data.get("max_output")
        max_output = int(max_output) if max_output is not None else int(get_tool_config().get("shell", {}).get("max_output", 20000))

        def _run_unsandboxed() -> Tuple[int, str]:
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return completed.returncode, output

        def _run_sandboxed() -> Tuple[int, str]:
            return _run_sandboxed_command(command, workdir, timeout_sec)

        ran_sandbox = False
        try:
            if agent_mode == "super":
                returncode, output = await asyncio.to_thread(_run_unsandboxed)
            elif cmd_name in unrestricted_allowset:
                returncode, output = await asyncio.to_thread(_run_unsandboxed)
            else:
                ran_sandbox = True
                returncode, output = await asyncio.to_thread(_run_sandboxed)
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

            try:
                returncode, output = await asyncio.to_thread(_run_unsandboxed)
            except subprocess.TimeoutExpired:
                return "Command timed out."

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

            try:
                returncode, output = await asyncio.to_thread(_run_unsandboxed)
            except subprocess.TimeoutExpired:
                return "Command timed out."

        if not output:
            output = "(no output)"
        if max_output > 0 and len(output) > max_output:
            output = output[:max_output] + "\n... (truncated)"

        return f"[exit_code={returncode}]\n{output}"


class RgTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "rg"
        self.description = "Search file contents using ripgrep (rg)."
        self.parameters = [
            ToolParameter(
                name="pattern",
                type="string",
                description="Search pattern (rg syntax).",
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
                description="Max number of matches.",
                required=False,
                default=200
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
        max_results = int(max_results) if max_results is not None else 200

        rg_exe = _resolve_rg_executable(root)
        args = [rg_exe, "--line-number", "--column", "--no-heading", "--color", "never"]
        if not case_sensitive:
            args.append("-i")
        if glob:
            args.extend(["--glob", str(glob)])
        if max_results > 0:
            args.extend(["--max-count", str(max_results)])
        # Use "--" to prevent patterns starting with "-" from being parsed as flags.
        args.extend(["--", str(pattern), str(search_path)])

        def _run() -> Tuple[int, str]:
            completed = subprocess.run(
                args,
                cwd=str(root),
                capture_output=True,
                text=True
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return completed.returncode, output

        try:
            returncode, output = await asyncio.to_thread(_run)
        except FileNotFoundError:
            raise ValueError("rg is not available on this system.")

        if returncode == 2:
            raise ValueError(output.strip() or "rg failed.")
        if returncode == 1 and not output.strip():
            return "No matches."

        return output.strip() or "No matches."


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
