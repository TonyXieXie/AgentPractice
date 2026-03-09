from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_paths import get_app_config_path as resolve_runtime_app_config_path


_DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "llm": {
        "timeout_sec": 180.0,
        "retry": {
            "max_retries": 5,
            "base_delay_sec": 1.0,
            "max_delay_sec": 8.0,
        },
    },
    "agent": {
        "default_profile": "default",
        "react_max_iterations": 16,
    },
    "transport": {
        "http": {
            "host": "127.0.0.1",
            "port": 8000,
        },
        "ws": {
            "heartbeat_interval_sec": 30,
        },
    },
}

_CONFIG_PATH_OVERRIDE: Optional[Path] = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def set_app_config_path(path: Optional[str | Path]) -> None:
    global _CONFIG_PATH_OVERRIDE
    _CONFIG_PATH_OVERRIDE = None if path is None else Path(path).expanduser().resolve()


def _get_config_file_path() -> Path:
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE
    return resolve_runtime_app_config_path()


def get_app_config_path() -> str:
    return str(_get_config_file_path())


def _load_config_file() -> Dict[str, Any]:
    path = _get_config_file_path()
    if not path.exists() or path.is_dir():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_app_config() -> Dict[str, Any]:
    return _deep_merge(_DEFAULT_APP_CONFIG, _load_config_file())
