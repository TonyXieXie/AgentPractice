from pathlib import Path
from typing import Any, List, Tuple

from ..base import Tool, ToolParameter
from ..config import get_tool_config
from .common import _get_root_path, _maybe_create_snapshot, _parse_json_input, _resolve_path


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ReadFileTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "read_file"
        self.description = "Read a file inside the work path (line-based or smart block mode)."
        self.parameters = [
            ToolParameter(name="path", type="string", description="Relative path under the work path.", required=True),
            ToolParameter(name="mode", type="string", description="Read mode: lines or smart.", required=False, default="lines"),
            ToolParameter(name="start_line", type="number", description="Start line (1-based) for lines mode.", required=False),
            ToolParameter(name="line_count", type="number", description="Number of lines to read for lines mode.", required=False),
            ToolParameter(name="anchor_line", type="number", description="Anchor line (1-based) for smart mode.", required=False),
            ToolParameter(
                name="indent_level",
                type="number",
                description="Indent levels above anchor to include for smart mode (1 level = 4 spaces or 1 tab).",
                required=False,
            ),
            ToolParameter(name="max_chars", type="number", description="Max characters to output.", required=False),
            ToolParameter(name="encoding", type="string", description="Text encoding.", required=False, default="utf-8"),
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
            with open(file_path, "r", encoding=encoding, errors="replace") as file:
                for idx, raw_line in enumerate(file, start=1):
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
            ToolParameter(name="path", type="string", description="Directory path (absolute or relative to work path).", required=False),
            ToolParameter(name="max_depth", type="number", description="Max depth to traverse (default 2, max 5).", required=False),
            ToolParameter(name="max_entries", type="number", description="Max number of entries to output (default 100, max 500).", required=False),
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

            file_entries.sort(key=lambda entry: entry[0].name.lower())
            dir_entries.sort(key=lambda entry: entry[0].name.lower())

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
            ToolParameter(name="path", type="string", description="Relative path under the work path.", required=True),
            ToolParameter(name="content", type="string", description="Content to write.", required=True),
            ToolParameter(name="mode", type="string", description="write or append.", required=False, default="write"),
            ToolParameter(name="encoding", type="string", description="Text encoding.", required=False, default="utf-8"),
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
        with open(file_path, file_mode, encoding=encoding) as file:
            file.write(str(content))
        return f"[write_file] wrote {len(str(content))} chars to {file_path}"


__all__ = ["ReadFileTool", "WriteFileTool", "ListFilesTool"]

