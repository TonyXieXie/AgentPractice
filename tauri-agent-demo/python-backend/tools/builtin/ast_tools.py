import ast as py_ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ast_file_filter import collect_ast_files
from ast_settings import get_ast_settings

from ..base import Tool, ToolParameter
from ..config import get_tool_config
from .common import _get_allowed_roots, _get_root_path, _is_within_root, _parse_json_input, _resolve_path

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


__all__ = [
    "CodeAstTool",
    "_AST_EXT_LANGUAGE",
    "_AST_IGNORE_DIRS",
    "_ast_for_file",
    "_get_ast_config",
]
