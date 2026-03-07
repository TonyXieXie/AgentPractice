import asyncio
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from ..base import Tool, ToolParameter
from .common import (
    _get_root_path,
    _is_within_root,
    _parse_json_input,
    _resolve_path,
    _resolve_rg_executable,
)
from .shell_runtime import RunShellTool


class RgTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "rg"
        self.description = "Search file contents using ripgrep (rg)."
        self.parameters = [
            ToolParameter(name="pattern", type="string", description="Search pattern (regular expression, rg syntax).", required=True),
            ToolParameter(name="path", type="string", description="Search path (relative to work path).", required=False),
            ToolParameter(name="glob", type="string", description="Optional glob filter (e.g. '*.ts').", required=False),
            ToolParameter(name="case_sensitive", type="boolean", description="Case sensitive search.", required=False, default=False),
            ToolParameter(name="max_results", type="number", description="Max number of output lines.", required=False, default=100),
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
            completed = subprocess.run(args, cwd=str(root), capture_output=True, text=True)
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

        results: List[str] = []
        grouped: Dict[str, List[str]] = {}
        root_path = root.resolve()

        def _to_relative_path(raw_path: str) -> str:
            try:
                path_obj = Path(raw_path)
                if not path_obj.is_absolute():
                    return str(path_obj)
                path_obj = path_obj.resolve()
                if _is_within_root(path_obj, root_path):
                    return os.path.relpath(str(path_obj), str(root_path))
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


__all__ = ["RunShellTool", "RgTool"]


