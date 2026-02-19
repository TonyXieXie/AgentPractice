import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_config import get_app_config_path


DEFAULT_MAX_FILES = 500
_SUPPORTED_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "tsx",
    "c",
    "cpp",
    "rust",
    "json"
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
_DEFAULT_SETTINGS: Dict[str, Any] = {
    "ignore_paths": [],
    "include_only_paths": [],
    "force_include_paths": [],
    "include_languages": [],
    "max_files": DEFAULT_MAX_FILES
}

_SETTINGS_LOCK = threading.RLock()
_SETTINGS_CACHE: Optional[Dict[str, Any]] = None


def normalize_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def path_key(path: Path) -> str:
    return str(path).lower()


def is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _get_settings_path() -> Path:
    config_path = Path(get_app_config_path())
    return config_path.parent / "ast_settings.json"


def _load_settings_file() -> Dict[str, Any]:
    path = _get_settings_path()
    if path.exists() and path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_settings_file(data: Dict[str, Any]) -> None:
    path = _get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_store() -> Dict[str, Any]:
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE
    data = _load_settings_file()
    if not isinstance(data, dict):
        data = {}
    paths = data.get("paths")
    if not isinstance(paths, dict):
        data["paths"] = {}
    _SETTINGS_CACHE = data
    return data


def _normalize_paths(root: Path, values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    result: List[str] = []
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
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(str(path))
    return result


def _normalize_max_files(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_FILES
    if parsed <= 0:
        return DEFAULT_MAX_FILES
    if parsed > 200000:
        return 200000
    return parsed


def _normalize_languages(values: Any) -> List[str]:
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
        if not normalized or normalized not in _SUPPORTED_LANGUAGES:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_settings(root: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(_DEFAULT_SETTINGS)
    if isinstance(settings, dict):
        normalized.update(settings)
    normalized["ignore_paths"] = _normalize_paths(root, normalized.get("ignore_paths"))
    normalized["include_only_paths"] = _normalize_paths(root, normalized.get("include_only_paths"))
    normalized["force_include_paths"] = _normalize_paths(root, normalized.get("force_include_paths"))
    normalized["include_languages"] = _normalize_languages(normalized.get("include_languages"))
    normalized["max_files"] = _normalize_max_files(normalized.get("max_files"))
    normalized["root"] = str(root)
    return normalized


def get_ast_settings(root: str) -> Dict[str, Any]:
    root_path = normalize_path(root)
    key = path_key(root_path)
    with _SETTINGS_LOCK:
        store = _get_store()
        entry = store.get("paths", {}).get(key, {})
    return _normalize_settings(root_path, entry if isinstance(entry, dict) else {})


def update_ast_settings(root: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    root_path = normalize_path(root)
    key = path_key(root_path)
    if not isinstance(patch, dict):
        patch = {}
    with _SETTINGS_LOCK:
        store = _get_store()
        paths = store.setdefault("paths", {})
        current = paths.get(key, {})
        if not isinstance(current, dict):
            current = {}
        merged = dict(current)
        for field in ("ignore_paths", "include_only_paths", "force_include_paths", "include_languages", "max_files"):
            if field in patch:
                merged[field] = patch.get(field)
        normalized = _normalize_settings(root_path, merged)
        paths[key] = {
            "ignore_paths": normalized.get("ignore_paths", []),
            "include_only_paths": normalized.get("include_only_paths", []),
            "force_include_paths": normalized.get("force_include_paths", []),
            "include_languages": normalized.get("include_languages", []),
            "max_files": normalized.get("max_files", DEFAULT_MAX_FILES)
        }
        _save_settings_file(store)
        _SETTINGS_CACHE = store
    return normalized


def get_all_ast_settings() -> Dict[str, Any]:
    with _SETTINGS_LOCK:
        store = _get_store()
        paths = store.get("paths", {}) if isinstance(store, dict) else {}

    entries: List[Dict[str, Any]] = []
    if isinstance(paths, dict):
        for key, entry in paths.items():
            if not isinstance(entry, dict):
                continue
            try:
                root_path = normalize_path(str(key))
            except Exception:
                root_path = Path(str(key))
            normalized = _normalize_settings(root_path, entry)
            entries.append({
                "root": normalized.get("root", str(root_path)),
                "settings": {
                    "ignore_paths": normalized.get("ignore_paths", []),
                    "include_only_paths": normalized.get("include_only_paths", []),
                    "force_include_paths": normalized.get("force_include_paths", []),
                    "include_languages": normalized.get("include_languages", []),
                    "max_files": normalized.get("max_files", DEFAULT_MAX_FILES)
                }
            })

    return {"paths": entries}
