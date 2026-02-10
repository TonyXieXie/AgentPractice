import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ast_settings import normalize_path, is_within_root

_LANGUAGE_EXTENSIONS = {
    "python": {".py"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "typescript": {".ts"},
    "tsx": {".tsx"},
    "c": {".c"},
    "cpp": {".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".inl"},
    "rust": {".rs"},
    "json": {".json"}
}
_LANGUAGE_ALIASES = {
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


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _is_under_any(path: Path, candidates: List[Path]) -> bool:
    for base in candidates:
        if _is_relative_to(path, base):
            return True
    return False


def _has_descendant(path: Path, candidates: List[Path]) -> bool:
    for candidate in candidates:
        if _is_relative_to(candidate, path):
            return True
    return False


def _normalize_settings_paths(root: Path, values: Any) -> List[Path]:
    if not isinstance(values, list):
        return []
    result: List[Path] = []
    seen = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        try:
            path = path.expanduser().resolve()
        except Exception:
            continue
        if not is_within_root(path, root):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _normalize_language_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    result: List[str] = []
    seen = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        key = raw.strip().lower()
        if not key:
            continue
        normalized = _LANGUAGE_ALIASES.get(key)
        if not normalized or normalized not in _LANGUAGE_EXTENSIONS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _extensions_for_languages(settings: Optional[Dict[str, Any]]) -> Optional[Set[str]]:
    if not settings:
        return None
    languages = _normalize_language_list(settings.get("include_languages"))
    if not languages:
        return None
    extensions: Set[str] = set()
    for lang in languages:
        extensions.update(_LANGUAGE_EXTENSIONS.get(lang, set()))
    return extensions or None


@lru_cache(maxsize=1)
def _git_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False


def _find_git_root(path: Path) -> Optional[Path]:
    current = path
    for parent in [current, *current.parents]:
        git_dir = parent / ".git"
        if git_dir.exists():
            return parent
    return None


def _filter_git_ignored(paths: List[Path], git_root: Optional[Path]) -> List[Path]:
    if not paths:
        return []
    if not git_root or not _git_available():
        return paths
    rel_paths: List[str] = []
    mapping: Dict[str, Path] = {}
    for path in paths:
        try:
            rel = path.relative_to(git_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        rel_paths.append(rel)
        mapping[rel] = path
    if not rel_paths:
        return paths
    input_data = ("\0".join(rel_paths)).encode("utf-8", errors="ignore")
    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), "check-ignore", "-z", "--stdin"],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False
        )
    except Exception:
        return paths
    if result.returncode not in (0, 1):
        return paths
    output = result.stdout.decode("utf-8", errors="ignore")
    ignored = {item for item in output.split("\0") if item}
    if not ignored:
        return paths
    kept: List[Path] = []
    for rel in rel_paths:
        if rel in ignored:
            continue
        path = mapping.get(rel)
        if path is not None:
            kept.append(path)
    return kept


def _should_descend_dir(
    dir_path: Path,
    ignore_dir_names: Set[str],
    include_only: List[Path],
    force_include: List[Path],
    ignore_paths: List[Path]
) -> bool:
    is_forced = _is_under_any(dir_path, force_include)
    has_forced_desc = _has_descendant(dir_path, force_include)
    has_included_desc = _has_descendant(dir_path, include_only)

    if dir_path.name in ignore_dir_names and not is_forced and not has_forced_desc and not has_included_desc:
        return False

    if ignore_paths and _is_under_any(dir_path, ignore_paths) and not is_forced and not has_forced_desc:
        return False

    if include_only or force_include:
        relevant = False
        for target in include_only + force_include:
            if _is_relative_to(dir_path, target) or _is_relative_to(target, dir_path):
                relevant = True
                break
        if not relevant:
            return False

    return True


def should_include_file(
    file_path: Path,
    settings_root: Path,
    settings: Optional[Dict[str, Any]],
    ignore_dir_names: Set[str]
) -> bool:
    root = normalize_path(settings_root)
    if not _is_relative_to(file_path, root):
        return False
    settings = settings or {}
    language_exts = _extensions_for_languages(settings)
    if language_exts and file_path.suffix.lower() not in language_exts:
        return False
    include_only = _normalize_settings_paths(root, settings.get("include_only_paths"))
    force_include = _normalize_settings_paths(root, settings.get("force_include_paths"))
    ignore_paths = _normalize_settings_paths(root, settings.get("ignore_paths"))

    if not _should_descend_dir(file_path.parent, ignore_dir_names, include_only, force_include, ignore_paths):
        return False

    is_forced = _is_under_any(file_path, force_include)
    is_custom_include = is_forced or (include_only and _is_under_any(file_path, include_only))
    if include_only and not is_custom_include:
        return False
    if ignore_paths and _is_under_any(file_path, ignore_paths) and not is_forced:
        return False

    git_root = _find_git_root(root)
    if git_root and not is_custom_include:
        kept = _filter_git_ignored([file_path], git_root)
        return bool(kept)
    return True


def collect_ast_files(
    scan_root: Path,
    settings_root: Optional[Path],
    extensions: Optional[List[str]],
    max_files: int,
    ignore_dir_names: Set[str],
    settings: Optional[Dict[str, Any]] = None
) -> List[Path]:
    root = normalize_path(settings_root or scan_root)
    scan_root = normalize_path(scan_root)
    if not _is_relative_to(scan_root, root):
        return []
    settings = settings or {}
    language_exts = _extensions_for_languages(settings)
    include_only = _normalize_settings_paths(root, settings.get("include_only_paths"))
    force_include = _normalize_settings_paths(root, settings.get("force_include_paths"))
    ignore_paths = _normalize_settings_paths(root, settings.get("ignore_paths"))

    norm_exts: Optional[Set[str]] = None
    if extensions:
        norm_exts = {ext.lower() if ext.startswith(".") else "." + ext.lower() for ext in extensions}
    if language_exts:
        if norm_exts is None:
            norm_exts = set(language_exts)
        else:
            norm_exts = norm_exts.intersection(language_exts)
        if not norm_exts:
            return []

    git_root = _find_git_root(root)
    collected: List[Path] = []
    pending: List[Path] = []
    batch_size = 200
    limit = max_files if max_files and max_files > 0 else None

    def flush_pending() -> bool:
        nonlocal pending, collected
        if not pending:
            return False
        kept = _filter_git_ignored(pending, git_root)
        pending = []
        for item in kept:
            collected.append(item)
            if limit and len(collected) >= limit:
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(scan_root):
        dir_path = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if _should_descend_dir(dir_path / name, ignore_dir_names, include_only, force_include, ignore_paths)
        ]

        for filename in filenames:
            path = dir_path / filename
            if norm_exts is not None and path.suffix.lower() not in norm_exts:
                continue
            if not _should_descend_dir(dir_path, ignore_dir_names, include_only, force_include, ignore_paths):
                continue
            is_forced = _is_under_any(path, force_include)
            is_custom_include = is_forced or (include_only and _is_under_any(path, include_only))
            if include_only and not is_custom_include:
                continue
            if ignore_paths and _is_under_any(path, ignore_paths) and not is_forced:
                continue

            if is_custom_include or not git_root:
                collected.append(path)
            else:
                pending.append(path)
                if len(pending) >= batch_size:
                    if flush_pending():
                        return collected[:limit] if limit else collected

            if limit and len(collected) >= limit:
                return collected[:limit]

        if limit and len(collected) >= limit:
            break

    flush_pending()
    return collected[:limit] if limit else collected
