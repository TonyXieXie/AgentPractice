import json
import os
from pathlib import Path
from typing import Any, Dict


_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": {
        "rg": True,
        "apply_patch": True,
        "search": True,
        "read_file": True,
        "write_file": True,
        "run_shell": True,
        "calculator": False,
        "weather": False
    },
    "files": {
        "max_bytes": 20000
    },
    "shell": {
        "allowlist": [
            "npm",
            "npx",
            "pnpm",
            "yarn",
            "node",
            "python",
            "pip",
            "git",
            "rg"
        ],
        "timeout_sec": 30,
        "max_output": 20000,
        "permission_timeout_sec": 300
    },
    "search": {
        "provider": "tavily",
        "max_results": 5,
        "search_depth": "basic",
        "min_score": 0.4
    }
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_config_path(path: Path) -> Path:
    if path.exists() and path.is_file():
        return path
    if path.suffix:
        return path
    return path / "tools_config.json"


def _get_config_file_path() -> Path:
    env_path = os.getenv("TOOLS_CONFIG_PATH")
    if env_path:
        return _normalize_config_path(Path(env_path))

    tauri_data_dir = os.getenv("TAURI_AGENT_DATA_DIR")
    if tauri_data_dir:
        return _normalize_config_path(Path(tauri_data_dir))

    root = _get_project_root()
    default_path = root / "tools_config.json"
    if default_path.exists():
        return default_path

    backend_path = root / "python-backend" / "tools_config.json"
    if backend_path.exists():
        return backend_path

    return default_path


def get_tool_config_path() -> str:
    return str(_get_config_file_path())


def _load_config_file() -> Dict[str, Any]:
    path = _get_config_file_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_config() -> Dict[str, Any]:
    config = _deep_merge(_DEFAULT_CONFIG, _load_config_file())
    root_override = os.getenv("TOOLS_PROJECT_ROOT") or config.get("project_root")
    root_path = Path(root_override) if root_override else _get_project_root()
    config["project_root"] = str(root_path.resolve())

    search = config.setdefault("search", {})
    if not search.get("tavily_api_key"):
        search["tavily_api_key"] = os.getenv("TAVILY_API_KEY", "")

    return config


_TOOL_CONFIG = _load_config()


def get_tool_config() -> Dict[str, Any]:
    return _TOOL_CONFIG


def update_tool_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _TOOL_CONFIG
    if not isinstance(patch, dict):
        raise ValueError("Config update must be a JSON object.")
    current_file = _load_config_file()
    merged_file = _deep_merge(current_file, patch)
    path = _get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged_file, ensure_ascii=False, indent=2), encoding="utf-8")
    _TOOL_CONFIG = _load_config()
    return _TOOL_CONFIG


def is_tool_enabled(name: str) -> bool:
    enabled = get_tool_config().get("enabled", {})
    return bool(enabled.get(name, False))
