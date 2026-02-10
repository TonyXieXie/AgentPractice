import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tree_sitter_languages import get_language, get_parser

from ast_index import get_ast_index
from app_config import get_app_config
from database import db


_WORD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_CLASS_NODE_TYPES = {
    "ClassDef",
    "class_declaration",
    "class_specifier",
    "struct_specifier",
    "union_specifier"
}

_C_CLASS_NODE_TYPES = {
    "struct_specifier",
    "union_specifier",
    "class_specifier"
}

_FUNCTION_NODE_TYPES = {
    "FunctionDef",
    "AsyncFunctionDef",
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition"
}

_C_FUNCTION_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "function_declarator"
}

_C_TYPE_NODE_TYPES = {
    "type_definition",
    "enum_specifier"
}

_C_SPECIFIER_NODE_TYPES = {
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "class_specifier"
}

_C_BODY_NODE_TYPES = {
    "field_declaration_list",
    "declaration_list"
}

_VARIABLE_DEF_TYPES = {
    "Assign",
    "AnnAssign",
    "declaration",
    "field_declaration",
    "variable_declarator",
    "lexical_declaration"
}

_CALL_NODE_TYPES = {
    "Call",
    "call_expression"
}

_IDENTIFIER_TYPES = {
    "Name",
    "identifier",
    "field_identifier",
    "qualified_identifier",
    "scoped_identifier",
    "namespace_identifier",
    "type_identifier"
}

_DEF_CONTEXT_TYPES = {
    "FunctionDef",
    "AsyncFunctionDef",
    "ClassDef",
    "class_declaration",
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "parameter_declaration",
    "init_declarator",
    "declarator",
    "field_declaration",
    "variable_declarator",
    "lexical_declaration"
}

_FUNC_DECLARATOR_TYPES = {
    "function_declarator",
    "declarator"
}

_TAG_QUERY_LOCK = threading.RLock()
_TAG_QUERY_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_TAG_QUERY_FILES = {
    "c": ["c-tags.scm"],
    "cpp": ["cpp-tags.scm"],
    "javascript": ["javascript-tags.scm"],
    "typescript": ["typescript-tags.scm"],
    "tsx": ["tsx-tags.scm"],
    "python": ["python-tags.scm"],
    "rust": ["rust-tags.scm"]
}

@dataclass
class SymbolDef:
    name: str
    kind: str
    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    parent: Optional[str] = None
    scope: Optional[str] = None
    code: Optional[str] = None
    score: float = 0.0
    ref_count: int = 0
    mention_count: int = 0
    ref_files: Optional[Dict[str, int]] = None


def _node_text(node: Dict[str, Any]) -> str:
    return str(node.get("text") or node.get("name") or node.get("attr") or "")


def _node_range(node: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    start = node.get("start") or []
    end = node.get("end") or []
    try:
        start_line = int(start[0]) if start else None
        end_line = int(end[0]) if end else None
    except Exception:
        return None, None
    return start_line, end_line


def _iter_nodes(node: Dict[str, Any], parent: Optional[Dict[str, Any]] = None):
    yield node, parent
    for child in node.get("children") or []:
        yield from _iter_nodes(child, node)


def _identifier_parts(node: Dict[str, Any]) -> List[str]:
    parts: List[str] = []
    for child, _ in _iter_nodes(node):
        if child.get("type") in _IDENTIFIER_TYPES:
            text = _node_text(child)
            if text:
                parts.append(text)
    return parts


def _extract_name(node: Dict[str, Any]) -> Optional[str]:
    if node.get("type") in _IDENTIFIER_TYPES:
        text = _node_text(node)
        return text or None
    parts = _identifier_parts(node)
    if not parts:
        return None
    return parts[-1]


def _find_first(node: Dict[str, Any], types: set) -> Optional[Dict[str, Any]]:
    if node.get("type") in types:
        return node
    for child in node.get("children") or []:
        found = _find_first(child, types)
        if found is not None:
            return found
    return None


def _extract_function_name(node: Dict[str, Any]) -> Optional[str]:
    declarator = _find_first(node, _FUNC_DECLARATOR_TYPES)
    if declarator is not None:
        name = _extract_name(declarator) or _extract_full_name(declarator)
        if name:
            return name
    return _extract_name(node) or _extract_full_name(node)


def _extract_full_name(node: Dict[str, Any]) -> Optional[str]:
    parts = _identifier_parts(node)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "::".join(parts)


def _direct_child(node: Dict[str, Any], node_type: str) -> Optional[Dict[str, Any]]:
    for child in node.get("children") or []:
        if child.get("type") == node_type:
            return child
    return None


def _has_body(node: Dict[str, Any]) -> bool:
    for child in node.get("children") or []:
        if child.get("type") in _C_BODY_NODE_TYPES:
            return True
    return False


def _qualified_identifier_name(node: Dict[str, Any]) -> Optional[str]:
    name = None
    for child, _ in _iter_nodes(node):
        if child.get("type") in ("identifier", "field_identifier"):
            text = _node_text(child)
            if text:
                name = text
    return name


def _has_node_type(node: Dict[str, Any], types: set, skip: Optional[Dict[str, Any]] = None) -> bool:
    if node is skip:
        return False
    if node.get("type") in types:
        return True
    for child in node.get("children") or []:
        if _has_node_type(child, types, skip):
            return True
    return False


def _has_return_type_sibling(parent: Optional[Dict[str, Any]], declarator: Dict[str, Any]) -> bool:
    if not parent:
        return False
    for child in parent.get("children") or []:
        if child is declarator:
            continue
        if _has_node_type(child, _C_RETURN_TYPE_NODE_TYPES) or _has_node_type(child, _C_SPECIFIER_NODE_TYPES):
            return True
    return False


def _ancestor_has_type(ancestor_types: List[str], types: set) -> bool:
    return any(ancestor in types for ancestor in ancestor_types)


def _extract_function_declarator_target(node: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    def walk(current: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        node_type = current.get("type")
        if node_type == "qualified_identifier":
            parts = _identifier_parts(current)
            if parts:
                name = parts[-1]
                scope = "::".join(parts[:-1]) if len(parts) > 1 else None
                return name, "method", scope
            return _qualified_identifier_name(current), "method", None
        if node_type in ("identifier", "field_identifier"):
            text = _node_text(current)
            return text or None, "function", None
        for child in current.get("children") or []:
            if child.get("type") == "parameter_list":
                return None, None, None
            name, kind, scope = walk(child)
            if name:
                return name, kind, scope
        return None, None, None

    for child in node.get("children") or []:
        if child.get("type") == "parameter_list":
            break
        name, kind, scope = walk(child)
        if name:
            return name, kind, scope
    return None, None, None


def _extract_typedef_name(node: Dict[str, Any]) -> Optional[str]:
    name: Optional[str] = None

    def walk(current: Dict[str, Any], in_spec: bool) -> None:
        nonlocal name
        node_type = current.get("type")
        next_in_spec = in_spec or node_type in _C_SPECIFIER_NODE_TYPES
        if node_type == "type_identifier" and not in_spec:
            text = _node_text(current)
            if text:
                name = text
        for child in current.get("children") or []:
            walk(child, next_in_spec)

    walk(node, False)
    return name


def _query_roots(work_path: Optional[str]) -> Tuple[List[Path], List[Path]]:
    work_roots: List[Path] = []
    if work_path:
        base = Path(work_path)
        work_roots.append(base / "queries")
        work_roots.append(base / "queries" / "tree-sitter-language-pack")
    backend_dir = Path(__file__).resolve().parent
    repo_root = backend_dir.parent
    builtin_roots = [
        backend_dir / "queries",
        repo_root / "queries",
        repo_root / "queries" / "tree-sitter-language-pack"
    ]

    def dedupe(items: List[Path]) -> List[Path]:
        seen: set = set()
        out: List[Path] = []
        for root in items:
            try:
                resolved = root.resolve()
            except Exception:
                resolved = root
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(resolved)
        return out

    return dedupe(work_roots), dedupe(builtin_roots)


def _query_file_names(language: str) -> List[str]:
    lang = (language or "").lower()
    if not lang:
        return []
    names = _TAG_QUERY_FILES.get(lang)
    if names:
        return names
    return [f"{lang}-tags.scm"]


def _load_tag_query(work_path: Optional[str], language: Optional[str]):
    if not language:
        return None
    lang = language.lower()
    work_roots, builtin_roots = _query_roots(work_path)
    names = _query_file_names(lang)
    if not names:
        return None

    sources: List[Path] = []
    seen: set = set()
    for root in work_roots + builtin_roots:
        common = root / "common-tags.scm"
        if common.is_file():
            key = str(common).lower()
            if key not in seen:
                seen.add(key)
                sources.append(common)

    lang_sources: List[Path] = []
    for root in work_roots:
        for name in names:
            path = root / name
            if not path.is_file():
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            lang_sources.append(path)
    if not lang_sources:
        for root in builtin_roots:
            for name in names:
                path = root / name
                if not path.is_file():
                    continue
                key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                lang_sources.append(path)

    sources.extend(lang_sources)

    if not sources:
        return None

    source_keys = [str(path) for path in sources]
    mtimes: List[float] = []
    for path in sources:
        try:
            mtimes.append(path.stat().st_mtime)
        except Exception:
            mtimes.append(0.0)

    if work_path:
        try:
            root_key = str(Path(work_path).resolve()).lower()
        except Exception:
            root_key = str(work_path).lower()
    else:
        root_key = ""
    cache_key = (root_key, lang)
    with _TAG_QUERY_LOCK:
        cached = _TAG_QUERY_CACHE.get(cache_key)
        if cached and cached.get("sources") == source_keys and cached.get("mtimes") == mtimes:
            return cached.get("query")

    query_text = ""
    for path in sources:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if content:
            query_text += content + "\n"
    if not query_text.strip():
        return None

    try:
        lang_obj = get_language(lang)
        query = lang_obj.query(query_text)
    except Exception as exc:
        with _TAG_QUERY_LOCK:
            _TAG_QUERY_CACHE[cache_key] = {
                "sources": source_keys,
                "mtimes": mtimes,
                "query": None,
                "error": str(exc)
            }
        return None

    with _TAG_QUERY_LOCK:
        _TAG_QUERY_CACHE[cache_key] = {
            "sources": source_keys,
            "mtimes": mtimes,
            "query": query,
            "error": None
        }
    return query


def _ts_node_text(source: bytes, node: Any, limit: int = 200) -> str:
    if node is None:
        return ""
    try:
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""
    if limit and len(text) > limit:
        return text[:limit]
    return text


def _ts_node_range(node: Any) -> Tuple[Optional[int], Optional[int]]:
    if node is None:
        return None, None
    try:
        start_line = int(node.start_point[0]) + 1
        end_line = int(node.end_point[0]) + 1
        return start_line, end_line
    except Exception:
        return None, None


def _capture_kind(capture: str) -> Optional[str]:
    if not capture:
        return None
    if ".definition." in capture:
        return capture.split(".definition.", 1)[1]
    if capture.startswith("definition."):
        return capture.split("definition.", 1)[1]
    return None


def _normalize_def_kind(kind: str) -> str:
    if not kind:
        return kind
    if kind in ("method", "function"):
        return "function"
    if kind in ("class", "struct", "union"):
        return "class"
    if kind in ("type", "typedef", "enum"):
        return "type"
    return kind


def _ts_guess_name(node: Any, source: bytes) -> Optional[str]:
    if node is None:
        return None
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        name = _ts_node_text(source, name_node, 120).strip()
        if name:
            return name
    for child in node.named_children:
        if child.type in ("identifier", "field_identifier", "type_identifier", "qualified_identifier"):
            name = _ts_node_text(source, child, 120).strip()
            if name:
                return name
    return None


def _ts_parent_class_name(node: Any, source: bytes) -> Optional[str]:
    current = node
    while current is not None:
        if current.type in ("class_specifier", "struct_specifier", "union_specifier", "class_declaration"):
            name = _ts_guess_name(current, source)
            if name:
                return name
        current = current.parent
    return None


def _ts_parent_scopes(node: Any, source: bytes) -> List[str]:
    scopes: List[str] = []
    current = node
    while current is not None:
        if current.type in ("namespace_definition", "class_specifier", "struct_specifier", "union_specifier", "class_declaration"):
            name = _ts_guess_name(current, source)
            if name:
                scopes.append(name)
        current = current.parent
    scopes.reverse()
    return scopes


def _normalize_scope_parts(parts: List[str]) -> Optional[str]:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return None
    for part in cleaned:
        if "::" in part:
            return part
    seen: set = set()
    ordered: List[str] = []
    for part in cleaned:
        if part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return "::".join(ordered) if ordered else None


def _ts_find_first_node(node: Any, types: set) -> Optional[Any]:
    if node is None:
        return None
    if node.type in types:
        return node
    for child in node.named_children:
        found = _ts_find_first_node(child, types)
        if found is not None:
            return found
    return None


def _ts_scope_from_qualified(node: Any, source: bytes) -> Optional[str]:
    if node is None:
        return None
    parts: List[str] = []
    for child in node.named_children:
        if child.type in ("namespace_identifier", "identifier", "field_identifier", "type_identifier"):
            text = _ts_node_text(source, child, 120).strip()
            if text:
                parts.append(text)
    if len(parts) > 1:
        return "::".join(parts[:-1])
    return None


def _collect_defs_from_schema(
    file_path: str,
    language: Optional[str],
    work_path: Optional[str]
) -> Optional[List[SymbolDef]]:
    query = _load_tag_query(work_path, language)
    if query is None:
        return None
    try:
        source = Path(file_path).read_bytes()
    except Exception:
        return []
    try:
        parser = get_parser((language or "").lower())
        tree = parser.parse(source)
    except Exception:
        return []

    defs: List[SymbolDef] = []
    seen: set = set()
    try:
        matches = query.matches(tree.root_node)
    except Exception:
        matches = None

    if matches is None:
        for node, capture in query.captures(tree.root_node):
            cap_kind = _capture_kind(capture)
            if not cap_kind or not capture.startswith("name.definition."):
                continue
            name = _ts_node_text(source, node, 200).strip()
            if not name:
                continue
            norm_kind = _normalize_def_kind(cap_kind)
            if norm_kind not in ("class", "function", "type"):
                continue
            scope_node = node
            if scope_node is not None and scope_node.type in ("namespace_definition", "class_specifier", "struct_specifier", "union_specifier", "class_declaration"):
                scope_node = scope_node.parent
            scopes = _ts_parent_scopes(scope_node, source) if scope_node is not None else []
            scope = "::".join(scopes) if scopes else None
            start_line, end_line = _ts_node_range(node)
            parent = _ts_parent_class_name(scope_node, source) if scope_node is not None else None
            key = (norm_kind, name, scope, start_line, end_line)
            if key in seen:
                continue
            seen.add(key)
            defs.append(SymbolDef(
                name=name,
                kind=norm_kind,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                parent=parent,
                scope=scope
            ))
        return defs

    for _, captures in matches:
        name: Optional[str] = None
        kind: Optional[str] = None
        name_node = None
        def_node = None
        scope_parts: List[str] = []
        for node, capture in captures:
            cap_kind = _capture_kind(capture)
            if not cap_kind:
                continue
            if capture.startswith("name.definition."):
                name = _ts_node_text(source, node, 200).strip()
                name_node = node
                kind = cap_kind
            elif capture.startswith("definition."):
                def_node = node
                kind = cap_kind
            elif capture.startswith("local.scope"):
                scope_text = _ts_node_text(source, node, 200).strip()
                if scope_text:
                    scope_parts.append(scope_text)

        if not name and def_node is not None:
            name = _ts_guess_name(def_node, source)
        if not name or not kind:
            continue

        norm_kind = _normalize_def_kind(kind)
        if norm_kind not in ("class", "function", "type"):
            continue

        range_node = def_node or name_node
        scope_node = range_node
        if scope_node is not None and scope_node.type in ("namespace_definition", "class_specifier", "struct_specifier", "union_specifier", "class_declaration"):
            scope_node = scope_node.parent
        scopes = _ts_parent_scopes(scope_node, source) if scope_node is not None else []
        scope = _normalize_scope_parts(scope_parts)
        if scope and "::" not in scope and scopes:
            scope = "::".join(scopes + [scope])
        elif not scope:
            scope = "::".join(scopes) if scopes else None
        if not scope:
            search_node = def_node or name_node
            qnode = _ts_find_first_node(search_node, {"qualified_identifier"})
            scope = _ts_scope_from_qualified(qnode, source)
        start_line, end_line = _ts_node_range(range_node)
        parent = _ts_parent_class_name(scope_node, source) if scope_node is not None else None
        key = (norm_kind, name, scope, start_line, end_line)
        if key in seen:
            continue
        seen.add(key)
        defs.append(SymbolDef(
            name=name,
            kind=norm_kind,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            parent=parent,
            scope=scope
        ))
    return defs


def _is_top_level(parent: Optional[Dict[str, Any]]) -> bool:
    if not parent:
        return True
    return parent.get("type") in ("Module", "translation_unit")


def _collect_defs_from_ast(
    ast_root: Dict[str, Any],
    file_path: str,
    language: Optional[str] = None
) -> List[SymbolDef]:
    defs: List[SymbolDef] = []
    class_stack: List[str] = []
    namespace_stack: List[str] = []
    lang = (language or "").lower()
    c_like = lang in ("c", "cpp")
    def walk(
        node: Dict[str, Any],
        parent: Optional[Dict[str, Any]],
        ancestor_types: List[str]
    ) -> None:
        node_type = node.get("type")
        pushed_class = False
        pushed_namespace = False
        if c_like:
            if node_type == "namespace_definition":
                name = _extract_name(node)
                if name:
                    namespace_stack.append(name)
                    pushed_namespace = True

            if node_type == "struct_specifier":
                name_node = _direct_child(node, "type_identifier")
                class_name = _node_text(name_node) if name_node else None
                if class_name and _has_body(node):
                    start_line, end_line = _node_range(node)
                    scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                    defs.append(SymbolDef(
                        name=class_name,
                        kind="class",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None,
                        scope=scope
                    ))
                    class_stack.append(class_name)
                    pushed_class = True

            if node_type == "union_specifier":
                if parent and parent.get("type") == "declaration":
                    name_node = _direct_child(node, "type_identifier")
                    class_name = _node_text(name_node) if name_node else None
                    if class_name:
                        start_line, end_line = _node_range(node)
                        scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                        defs.append(SymbolDef(
                            name=class_name,
                            kind="class",
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                            parent=class_stack[-1] if class_stack else None,
                            scope=scope
                        ))
                        if _has_body(node):
                            class_stack.append(class_name)
                            pushed_class = True

            if node_type == "class_specifier":
                name_node = _direct_child(node, "type_identifier")
                class_name = _node_text(name_node) if name_node else None
                if class_name:
                    start_line, end_line = _node_range(node)
                    scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                    defs.append(SymbolDef(
                        name=class_name,
                        kind="class",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None,
                        scope=scope
                    ))
                    if _has_body(node):
                        class_stack.append(class_name)
                        pushed_class = True

            if node_type == "type_definition":
                name = _extract_typedef_name(node)
                if name:
                    start_line, end_line = _node_range(node)
                    scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                    defs.append(SymbolDef(
                        name=name,
                        kind="type",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None,
                        scope=scope
                    ))

            if node_type == "enum_specifier":
                name_node = _direct_child(node, "type_identifier")
                name = _node_text(name_node) if name_node else None
                if name:
                    start_line, end_line = _node_range(node)
                    scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                    defs.append(SymbolDef(
                        name=name,
                        kind="type",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None,
                        scope=scope
                    ))

            if node_type == "function_declarator":
                name, kind, scope_hint = _extract_function_declarator_target(node)
                if name:
                    in_function_scope = _ancestor_has_type(
                        ancestor_types,
                        {"function_definition", "function_declaration"}
                    )
                    parent_type = parent.get("type") if parent else None
                    has_return_type = False
                    if parent_type in ("declaration", "field_declaration", "init_declarator", "declarator"):
                        has_return_type = _has_return_type_sibling(parent, node)
                    is_ctor = False
                    if class_stack:
                        class_name = class_stack[-1]
                        is_ctor = name == class_name or name == f"~{class_name}"
                    if in_function_scope or has_return_type or is_ctor:
                        if scope_hint:
                            if "::" in scope_hint:
                                scope = scope_hint
                            elif namespace_stack:
                                scope = "::".join(namespace_stack + [scope_hint])
                            else:
                                scope = scope_hint
                        else:
                            scope = "::".join(namespace_stack + class_stack) if namespace_stack or class_stack else None
                        start_line, end_line = _node_range(node)
                        defs.append(SymbolDef(
                            name=name,
                            kind="function" if kind in ("function", "method") else "function",
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                            parent=class_stack[-1] if class_stack else None,
                            scope=scope
                        ))
        else:
            if node_type in _CLASS_NODE_TYPES:
                class_name = node.get("name") or _extract_name(node)
                if class_name:
                    start_line, end_line = _node_range(node)
                    defs.append(SymbolDef(
                        name=class_name,
                        kind="class",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None
                    ))
                    class_stack.append(class_name)
                    pushed_class = True

            if node_type in _FUNCTION_NODE_TYPES:
                name = node.get("name") or _extract_function_name(node)
                if name:
                    start_line, end_line = _node_range(node)
                    defs.append(SymbolDef(
                        name=name,
                        kind="function",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        parent=class_stack[-1] if class_stack else None
                    ))

            if node_type in _VARIABLE_DEF_TYPES:
                if not _is_top_level(parent) and node_type in ("Assign", "AnnAssign"):
                    pass
                elif not _is_top_level(parent) and node_type in ("declaration", "field_declaration"):
                    pass
                else:
                    name = _extract_name(node)
                    if name:
                        start_line, end_line = _node_range(node)
                        defs.append(SymbolDef(
                            name=name,
                            kind="variable",
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                            parent=class_stack[-1] if class_stack else None
                        ))

        for child in node.get("children") or []:
            walk(child, node, ancestor_types + [node_type])

        if pushed_class and class_stack:
            class_stack.pop()
        if pushed_namespace and namespace_stack:
            namespace_stack.pop()

    walk(ast_root, None, [])
    return defs


def _collect_refs_from_ast(
    ast_root: Dict[str, Any],
    file_path: str,
    allowed_names: Optional[set] = None
) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]]]:
    ref_counts: Dict[str, int] = {}
    ref_files: Dict[str, Dict[str, int]] = {}

    def record(name: str) -> None:
        if not name:
            return
        if allowed_names is not None and name not in allowed_names:
            return
        ref_counts[name] = ref_counts.get(name, 0) + 1
        file_map = ref_files.get(name)
        if file_map is None:
            file_map = {}
            ref_files[name] = file_map
        file_map[file_path] = file_map.get(file_path, 0) + 1

    for node, parent in _iter_nodes(ast_root, None):
        node_type = node.get("type")
        parent_type = parent.get("type") if parent else None
        if node_type in _CALL_NODE_TYPES:
            children = node.get("children") or []
            callee = children[0] if children else node
            name = _extract_name(callee) or _extract_full_name(callee)
            if name:
                record(name)
            continue
        if node_type in _IDENTIFIER_TYPES and node_type != "Name":
            if parent_type in _DEF_CONTEXT_TYPES:
                continue
            name = _node_text(node)
            if name:
                record(name)
            continue
        if node_type == "Name":
            if parent_type in _DEF_CONTEXT_TYPES:
                continue
            name = node.get("name") or _node_text(node)
            if name:
                record(name)

    return ref_counts, ref_files


def _read_snippet(file_path: str, start_line: Optional[int], end_line: Optional[int], max_lines: int) -> Optional[str]:
    if not start_line:
        return None
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    lines = text.split("\n")
    start = max(1, start_line)
    end = end_line if end_line and end_line >= start else start
    end = min(end, start + max_lines - 1)
    if start > len(lines):
        return None
    snippet = "\n".join(lines[start - 1:end])
    if end < (end_line or start):
        snippet = f"{snippet}\n... (truncated)"
    return snippet


def _count_mentions(name: str, messages: List[str]) -> int:
    if not name:
        return 0
    if _WORD_NAME.match(name):
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        return sum(len(pattern.findall(msg)) for msg in messages)
    return sum(msg.count(name) for msg in messages)


def _get_code_map_config() -> Dict[str, Any]:
    app_cfg = get_app_config()
    agent_cfg = app_cfg.get("agent", {}) if isinstance(app_cfg, dict) else {}
    ast_enabled = bool(agent_cfg.get("ast_enabled", True))
    code_cfg = agent_cfg.get("code_map", {}) if isinstance(agent_cfg, dict) else {}
    return {
        "ast_enabled": ast_enabled,
        "enabled": bool(code_cfg.get("enabled", True)),
        "max_symbols": int(code_cfg.get("max_symbols", 40)),
        "max_files": int(code_cfg.get("max_files", 20)),
        "max_lines": int(code_cfg.get("max_lines", 30)),
        "weight_refs": float(code_cfg.get("weight_refs", 1.0)),
        "weight_mentions": float(code_cfg.get("weight_mentions", 2.0))
    }


def build_code_map_prompt(session_id: str, work_path: Optional[str]) -> Optional[str]:
    if not work_path:
        return None
    cfg = _get_code_map_config()
    if not cfg.get("ast_enabled", True):
        return None
    if not cfg.get("enabled", True):
        return None

    index = get_ast_index().get_root(work_path)
    if not index.files:
        index.scan_root()
    else:
        last_scan = index.last_scan
        refresh_interval = 300
        if last_scan is None or (time.time() - last_scan) > refresh_interval:
            index.scan_root_async()

    entries = list(index.files.values())
    if not entries:
        return None

    all_defs: List[SymbolDef] = []
    ref_counts: Dict[str, int] = {}
    ref_files: Dict[str, Dict[str, int]] = {}

    ast_entries: List[Tuple[str, Dict[str, Any], Optional[str]]] = []
    for entry in entries:
        payload = index.ensure_file(Path(entry.path))
        ast_root = payload.get("ast")
        if not isinstance(ast_root, dict):
            continue
        language = payload.get("language")
        defs = _collect_defs_from_schema(entry.path, language, work_path)
        if defs is None:
            defs = _collect_defs_from_ast(ast_root, entry.path, language)
        all_defs.extend(defs)
        ast_entries.append((entry.path, ast_root, language))

    allowed_ref_names = {sym.name for sym in all_defs if sym.kind in ("class", "function")}
    for file_path, ast_root, _ in ast_entries:
        file_refs, file_ref_files = _collect_refs_from_ast(ast_root, file_path, allowed_ref_names)
        for name, count in file_refs.items():
            ref_counts[name] = ref_counts.get(name, 0) + count
        for name, file_map in file_ref_files.items():
            target = ref_files.setdefault(name, {})
            for ref_path, count in file_map.items():
                target[ref_path] = target.get(ref_path, 0) + count

    if not all_defs:
        return None

    messages = [
        getattr(msg, "content", "")
        for msg in db.get_session_messages(session_id)
        if getattr(msg, "role", None) == "user"
    ]
    for symbol in all_defs:
        if symbol.kind in ("class", "function"):
            symbol.ref_count = ref_counts.get(symbol.name, 0)
            symbol.ref_files = ref_files.get(symbol.name, {})
        else:
            symbol.ref_count = 0
            symbol.ref_files = {}
        symbol.mention_count = _count_mentions(symbol.name, messages)
        symbol.score = symbol.ref_count * cfg["weight_refs"] + symbol.mention_count * cfg["weight_mentions"]
        symbol.code = _read_snippet(symbol.file_path, symbol.start_line, symbol.end_line, cfg["max_lines"])

    all_defs.sort(key=lambda item: (-item.score, item.file_path, item.name))
    top_defs = all_defs[: cfg["max_symbols"]]
    if not top_defs:
        return None

    files: Dict[str, List[SymbolDef]] = {}
    for symbol in top_defs:
        files.setdefault(symbol.file_path, []).append(symbol)

    file_items = sorted(
        files.items(),
        key=lambda item: (-sum(sym.score for sym in item[1]), item[0])
    )[: cfg["max_files"]]

    lines: List[str] = []
    lines.append("Code map (top symbols with scores, references, and snippets).")
    lines.append(f"Score = refs*{cfg['weight_refs']} + mentions*{cfg['weight_mentions']}")
    lines.append("")

    for file_path, symbols in file_items:
        lines.append(f"File: {file_path}")
        for symbol in symbols:
            display_name = symbol.name
            if symbol.scope:
                display_name = f"{symbol.scope}::{symbol.name}"
            header = f"- {symbol.kind} {display_name}"
            if symbol.parent and not symbol.scope:
                header += f" (parent: {symbol.parent})"
            meta = f"score={symbol.score:.1f}, refs={symbol.ref_count}, mentions={symbol.mention_count}"
            if symbol.start_line:
                meta += f", lines {symbol.start_line}-{symbol.end_line or symbol.start_line}"
            header = f"{header} [{meta}]"
            lines.append(header)
            if symbol.code:
                snippet_lines = symbol.code.split("\n")
                lines.append("  ```")
                lines.extend([f"  {line}" for line in snippet_lines])
                lines.append("  ```")
        lines.append("")

    return "\n".join(lines).strip() if lines else None
