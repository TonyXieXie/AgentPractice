import difflib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_tool_config
from ..context import get_tool_context


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
        from repositories.permission_repository import create_permission_request

        tool_ctx = get_tool_context()
        session_id = tool_ctx.get("session_id")
        return create_permission_request(
            tool_name=tool_name,
            action=action,
            path=str(path),
            reason=reason,
            session_id=session_id,
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
            reason="Path outside work path.",
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
            from repositories import chat_repository

            message_id = chat_repository.get_latest_assistant_message_id(str(session_id))
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
            patches.append({"kind": "update", "path": path, "hunks": hunks})
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
            patches.append({"kind": "add", "path": path, "lines": add_lines})
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
            patches.append({"kind": "delete", "path": path})
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

            diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
            added, removed = _count_diff_changes(diff_lines)
            summary.append({"path": path, "added": added, "removed": removed})
            if diff_lines:
                diffs.append("\n".join(diff_lines))

            revert_diff = list(difflib.unified_diff(new_lines, old_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
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
            diff_lines = list(difflib.unified_diff([], add_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
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
            diff_lines = list(difflib.unified_diff(old_lines, [], fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
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

    return {"ok": True, "summary": summary, "diff": combined_diff, "revert_patch": revert_patch}


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


def _is_windows() -> bool:
    return os.name == "nt"


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


__all__ = [
    "_apply_patch_text",
    "_build_apply_patch_from_hunks",
    "_command_targets_outside_root",
    "_contains_shell_operators",
    "_count_diff_changes",
    "_extract_command_name",
    "_extract_path_candidates",
    "_filter_unified_hunks",
    "_find_all_matches",
    "_find_all_matches_relaxed",
    "_get_agent_mode",
    "_get_allowed_roots",
    "_get_root_path",
    "_get_session_id",
    "_is_windows",
    "_is_within_root",
    "_join_command",
    "_log_permission_request",
    "_looks_like_path",
    "_maybe_create_snapshot",
    "_normalize_match_line",
    "_normalize_newlines",
    "_parse_apply_patch",
    "_parse_json_input",
    "_resolve_path",
    "_resolve_rg_executable",
    "_rewrite_rg_command",
]

