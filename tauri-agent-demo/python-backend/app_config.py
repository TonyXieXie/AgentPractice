import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


_DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "llm": {
        "timeout_sec": 180.0
    }
}

_CONFIG_PATH_OVERRIDE: Optional[Path] = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _normalize_config_path(path: Path) -> Path:
    if path.exists() and path.is_dir():
        return path / "app_config.json"
    return path


def _get_config_file_path() -> Path:
    global _CONFIG_PATH_OVERRIDE
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE
    env_path = os.getenv("APP_CONFIG_PATH")
    if env_path:
        return _normalize_config_path(Path(env_path))

    root = _get_project_root()
    default_path = root / "app_config.json"
    if default_path.exists():
        return default_path

    backend_path = root / "python-backend" / "app_config.json"
    if backend_path.exists():
        return backend_path

    return default_path


def get_app_config_path() -> str:
    return str(_get_config_file_path())


def _load_config_file() -> Dict[str, Any]:
    path = _get_config_file_path()
    if path.exists() and not path.is_dir():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _coerce_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError("llm.timeout_sec must be a number")
    if timeout <= 0:
        raise ValueError("llm.timeout_sec must be positive")
    if timeout > 3600:
        raise ValueError("llm.timeout_sec must be <= 3600 seconds")
    return timeout


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config)
    llm = dict(normalized.get("llm", {}))
    if "timeout_sec" in llm:
        llm["timeout_sec"] = _coerce_timeout(llm["timeout_sec"])
    normalized["llm"] = llm
    return normalized


def _load_config() -> Dict[str, Any]:
    merged = _deep_merge(_DEFAULT_APP_CONFIG, _load_config_file())
    return _normalize_config(merged)


_APP_CONFIG = _load_config()


def get_app_config() -> Dict[str, Any]:
    return _APP_CONFIG


def update_app_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _APP_CONFIG
    global _CONFIG_PATH_OVERRIDE
    if not isinstance(patch, dict):
        raise ValueError("Config update must be a JSON object.")
    current_file = _load_config_file()
    merged_file = _deep_merge(current_file, patch)
    merged_file = _normalize_config(merged_file)
    path = _get_config_file_path()
    content = json.dumps(merged_file, ensure_ascii=False, indent=2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError:
        fallback_path = Path.home() / ".tauri-agent" / "app_config.json"
        if fallback_path != path:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            fallback_path.write_text(content, encoding="utf-8")
            _CONFIG_PATH_OVERRIDE = fallback_path
        else:
            raise
    _APP_CONFIG = _load_config()
    return _APP_CONFIG
