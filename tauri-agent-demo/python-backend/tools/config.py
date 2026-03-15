import json
import os
from pathlib import Path
from typing import Any, Dict

from runtime_paths import get_project_root, get_tools_config_path as resolve_runtime_tools_config_path


_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": {
        "rg": True,
        "apply_patch": True,
        "search": True,
        "read_file": True,
        "write_file": True,
        "list_files": True,
        "run_shell": True,
        "code_ast": True,
        "handoff": True,
        "spawn_subagent": True
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
        "unrestricted_allowlist": [],
        "timeout_sec": 120,
        "buffer_size": 2097152,
        "max_output": 20000,
        "permission_timeout_sec": 300,
        "oneshot_status_interval_sec": 1.0,
        "persistent_pty_blocks_steps": False
    },
    "search": {
        "provider": "tavily",
        "providers": ["tavily"],
        "max_results": 5,
        "search_depth": "basic",
        "min_score": 0.4,
        "gemini_model": "gemini-2.5-flash"
    },
    "ast": {
        "max_bytes": 200000,
        "max_nodes": 2000,
        "max_depth": 12,
        "max_files": 500,
        "max_symbols": 2000,
        "include_text": False
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


def _get_config_file_path() -> Path:
    return resolve_runtime_tools_config_path()


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
    root_path = Path(root_override) if root_override else get_project_root()
    config["project_root"] = str(root_path.resolve())

    search = config.setdefault("search", {})

    raw_providers = search.get("providers")
    providers = []
    if isinstance(raw_providers, list):
        providers = [str(item).strip().lower() for item in raw_providers if str(item).strip()]
    elif isinstance(raw_providers, str):
        providers = [item.strip().lower() for item in raw_providers.split(",") if item.strip()]

    legacy_provider = str(search.get("provider") or "").strip().lower()
    if not providers and legacy_provider:
        providers = [legacy_provider]
    if not providers:
        providers = ["tavily"]

    deduped_providers = []
    for provider in providers:
        if provider and provider not in deduped_providers:
            deduped_providers.append(provider)

    search["providers"] = deduped_providers
    search["provider"] = deduped_providers[0]

    if not search.get("tavily_api_key"):
        search["tavily_api_key"] = os.getenv("TAVILY_API_KEY", "")
    if not search.get("gemini_api_key"):
        search["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")
    if not search.get("gemini_model"):
        search["gemini_model"] = os.getenv("GEMINI_SEARCH_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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
