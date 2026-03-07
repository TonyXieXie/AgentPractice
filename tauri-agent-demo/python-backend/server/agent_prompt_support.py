import re
import time
from typing import List, Optional

from tools.pty_manager import get_pty_manager


PTY_PROMPT_CMD_MAX_CHARS = 160
PTY_PROMPT_PER_PTY_MAX_LINES = 120
PTY_PROMPT_PER_PTY_MAX_BYTES = 4 * 1024
PTY_PROMPT_TOTAL_MAX_BYTES = 12 * 1024
PTY_PROMPT_MAX_VISIBLE_PTYS = 64
_PTY_PROMPT_ANSI_RE = re.compile(r"[\u001b\u009b][\\[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[@-~]")
_PTY_PROMPT_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_PTY_PROMPT_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _truncate_text(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def _strip_pty_prompt_controls(text: str) -> str:
    value = str(text or "")
    value = _PTY_PROMPT_OSC_RE.sub("", value)
    value = _PTY_PROMPT_ANSI_RE.sub("", value)
    value = value.replace("\r", "")
    value = _PTY_PROMPT_CTRL_RE.sub("", value)
    return value


def _tail_lines(text: str, max_lines: int) -> str:
    lines = str(text or "").splitlines()
    if max_lines <= 0:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _tail_bytes_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = str(text or "").encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return str(text or "")
    tail = encoded[-max_bytes:]
    return tail.decode("utf-8", errors="ignore")


def build_live_pty_prompt(session_id: Optional[str]) -> str:
    if not session_id:
        return "None."
    try:
        items = get_pty_manager().list(session_id)
    except Exception:
        return "None."
    running = [item for item in items if item.status == "running"]
    if not running:
        return "None."

    running.sort(key=lambda item: item.last_output_at, reverse=True)
    running = running[:PTY_PROMPT_MAX_VISIBLE_PTYS]

    now = time.time()
    now_mono = time.monotonic()
    sections: List[str] = []
    remaining_total = PTY_PROMPT_TOTAL_MAX_BYTES

    for item in running:
        if remaining_total <= 0:
            break
        cmd = _truncate_text(item.command or "", PTY_PROMPT_CMD_MAX_CHARS) or "(no command)"
        idle_sec = max(0, int(now_mono - item.last_output_at))
        age_sec = max(0, int(now - item.created_at))

        try:
            snapshot = item.get_snapshot() if hasattr(item, "get_snapshot") else {}
        except Exception:
            snapshot = {}
        screen_text = str(snapshot.get("screen_text") or "")
        screen_hash = str(snapshot.get("screen_hash") or "")

        cleaned = _strip_pty_prompt_controls(screen_text)
        cleaned = _tail_lines(cleaned, PTY_PROMPT_PER_PTY_MAX_LINES)
        cleaned = _tail_bytes_utf8(cleaned, PTY_PROMPT_PER_PTY_MAX_BYTES)

        header = (
            f"### PTY {item.id}\n"
            f"- status: {item.status}\n"
            f"- mode: {getattr(item, 'pty_mode', 'ephemeral')}\n"
            f"- waiting_input: {str(bool(getattr(item, 'waiting_input', False))).lower()}\n"
            f"- wait_reason: {getattr(item, 'wait_reason', None) or 'none'}\n"
            f"- age_sec: {age_sec}\n"
            f"- idle_sec: {idle_sec}\n"
            f"- command: {cmd}\n"
        )
        if screen_hash:
            header += f"- screen_hash: {screen_hash}\n"
        content_block = cleaned if cleaned else "(no screen output)"
        section = f"{header}```text\n{content_block}\n```"

        encoded = section.encode("utf-8", errors="replace")
        if len(encoded) > remaining_total:
            overhead = len((f"{header}```text\n\n```").encode("utf-8", errors="replace"))
            allowed_body = max(0, remaining_total - overhead)
            trimmed_body = _tail_bytes_utf8(content_block, allowed_body)
            section = f"{header}```text\n{trimmed_body}\n```"
            encoded = section.encode("utf-8", errors="replace")
            if len(encoded) > remaining_total:
                break
        sections.append(section)
        remaining_total -= len(encoded)

    if not sections:
        return "None."

    hidden = max(0, len(running) - len(sections))
    if hidden > 0:
        sections.append(f"... and {hidden} more running PTY session(s) omitted by budget.")
    return "\n\n".join(sections)


def append_reasoning_summary_prompt(system_prompt: str, reasoning_summary: Optional[str]) -> str:
    if not reasoning_summary:
        return system_prompt
    summary = str(reasoning_summary).strip().lower()
    if summary == "concise":
        instruction = "If you include reasoning summaries, keep them concise (1-3 short bullets)."
    elif summary == "detailed":
        instruction = "If you include reasoning summaries, make them detailed and step-by-step; keep final answers concise."
    else:
        instruction = "Provide a reasoning summary only when helpful; otherwise answer directly."
    block = f"## Reasoning Summary\n{instruction}"
    if not system_prompt:
        return block
    return f"{system_prompt}\n\n{block}"


__all__ = ["append_reasoning_summary_prompt", "build_live_pty_prompt"]
