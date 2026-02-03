import asyncio
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

import httpx

from ..base import Tool, ToolParameter
from ..config import get_tool_config, update_tool_config


def _get_root_path() -> Path:
    root = get_tool_config().get("project_root")
    return Path(root).resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    root_str = str(root).lower()
    path_str = str(path).lower()
    if path_str == root_str:
        return True
    return path_str.startswith(root_str + os.sep)


def _log_permission_request(tool_name: str, action: str, path: Path, reason: str) -> Optional[int]:
    try:
        from database import db
        return db.create_permission_request(
            tool_name=tool_name,
            action=action,
            path=str(path),
            reason=reason
        )
    except Exception:
        return None


def _resolve_path(raw_path: str, tool_name: str, action: str) -> Path:
    root = _get_root_path()
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not _is_within_root(path, root):
        request_id = _log_permission_request(
            tool_name=tool_name,
            action=action,
            path=path,
            reason="Path outside project root."
        )
        suffix = f" Request ID: {request_id}" if request_id else ""
        raise PermissionError(f"Permission required for paths outside project root.{suffix}")
    return path


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
        self.description = "Read a file inside the project root."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Relative path under project root.",
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
        self.description = "Write content to a file inside the project root."
        self.parameters = [
            ToolParameter(
                name="path",
                type="string",
                description="Relative path under project root.",
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
        self.description = "Run a shell command from the allowlist within the project root."
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
                description="Working directory (relative to project root).",
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
        allowlist = get_tool_config().get("shell", {}).get("allowlist", [])
        allowset = {str(item).lower() for item in allowlist}
        reasons = []
        if _contains_shell_operators(command):
            reasons.append("Shell operators detected.")
        if cmd_name not in allowset:
            reasons.append("Command not in allowlist.")
        if reasons:
            request_id = None
            try:
                from database import db
                request_id = db.create_permission_request(
                    tool_name=self.name,
                    action="execute",
                    path=str(command),
                    reason=" ".join(reasons)
                )
            except Exception:
                request_id = None

            timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
            status = await _wait_for_permission(request_id, timeout_sec)
            if status != "approved":
                if status == "denied":
                    return "Permission denied."
                if status == "timeout":
                    return "Permission request timed out."
                return "Permission required."

            if cmd_name not in allowset:
                _ensure_shell_allowlist_entry(cmd_name)

        root = _get_root_path()
        cwd = data.get("cwd")
        workdir = _resolve_path(cwd, self.name, "execute") if cwd else root

        timeout = data.get("timeout_sec")
        timeout_sec = float(timeout) if timeout is not None else float(get_tool_config().get("shell", {}).get("timeout_sec", 30))
        max_output = data.get("max_output")
        max_output = int(max_output) if max_output is not None else int(get_tool_config().get("shell", {}).get("max_output", 20000))

        def _run() -> Tuple[int, str]:
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

        try:
            returncode, output = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return "Command timed out."

        if not output:
            output = "(no output)"
        if max_output > 0 and len(output) > max_output:
            output = output[:max_output] + "\n... (truncated)"

        return f"[exit_code={returncode}]\n{output}"


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
