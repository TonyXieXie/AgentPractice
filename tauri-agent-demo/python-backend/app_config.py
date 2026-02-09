import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


_DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "llm": {
        "timeout_sec": 180.0
    },
    "context": {
        "compression_enabled": False,
        "compress_start_pct": 75,
        "compress_target_pct": 55,
        "min_keep_messages": 12,
        "keep_recent_calls": 10,
        "step_calls": 5,
        "truncate_long_data": True,
        "long_data_threshold": 4000,
        "long_data_head_chars": 1200,
        "long_data_tail_chars": 800
    },
    "agent": {
        "base_system_prompt": "You are a helpful AI assistant.",
        "react_max_iterations": 50,
        "abilities": [
            {
                "id": "tools_all",
                "name": "All Tools",
                "type": "tooling",
                "tools": ["*"],
                "prompt": ""
            },
            {
                "id": "rg_search",
                "name": "RG Search",
                "type": "tooling",
                "tools": ["rg"],
                "prompt": "Prefer rg for searching file contents."
            },
            {
                "id": "apply_patch",
                "name": "Apply Patch",
                "type": "tooling",
                "tools": ["apply_patch"],
                "prompt": "Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\napply_patch format (strict):\n  *** Begin Patch\n  *** Update File: path\n  @@\n  - old line\n  + new line\n  *** End Patch\n- Each change line must start with + or -, and context lines must be included under @@ hunks.\n- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n- apply_patch matches by context; if the match is not unique, request more surrounding context.\n- If apply_patch fails due to context, ask for more context and retry."
            },
            {
                "id": "tool_json",
                "name": "Tool Arguments JSON",
                "type": "tool_policy",
                "prompt": "If a tool is needed, call it with JSON arguments that match its schema."
            },
            {
                "id": "output_concise",
                "name": "Concise Output",
                "type": "output_format",
                "prompt": "Be concise and actionable."
            }
        ],
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "abilities": ["tools_all", "rg_search", "apply_patch", "tool_json", "output_concise"]
            }
        ],
        "default_profile": "default"
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

    tauri_data_dir = os.getenv("TAURI_AGENT_DATA_DIR")
    if tauri_data_dir:
        return _normalize_config_path(Path(tauri_data_dir))

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


def _coerce_react_max_iterations(value: Any) -> int:
    try:
        max_iterations = int(value)
    except (TypeError, ValueError):
        raise ValueError("agent.react_max_iterations must be an integer")
    if max_iterations < 1:
        raise ValueError("agent.react_max_iterations must be >= 1")
    if max_iterations > 200:
        raise ValueError("agent.react_max_iterations must be <= 200")
    return max_iterations


def _coerce_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "y", "on"):
            return True
        if lowered in ("false", "0", "no", "n", "off"):
            return False
    raise ValueError(f"{field} must be a boolean")


def _coerce_int_range(value: Any, field: str, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{field} must be between {min_value} and {max_value}")
    return parsed


def _normalize_context_config(context: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(context)
    if "compression_enabled" in normalized:
        normalized["compression_enabled"] = _coerce_bool(
            normalized["compression_enabled"], "context.compression_enabled"
        )
    if "compress_start_pct" in normalized:
        normalized["compress_start_pct"] = _coerce_int_range(
            normalized["compress_start_pct"], "context.compress_start_pct", 1, 100
        )
    if "compress_target_pct" in normalized:
        normalized["compress_target_pct"] = _coerce_int_range(
            normalized["compress_target_pct"], "context.compress_target_pct", 1, 100
        )
    if "min_keep_messages" in normalized:
        normalized["min_keep_messages"] = _coerce_int_range(
            normalized["min_keep_messages"], "context.min_keep_messages", 1, 200
        )
    if "keep_recent_calls" in normalized:
        normalized["keep_recent_calls"] = _coerce_int_range(
            normalized["keep_recent_calls"], "context.keep_recent_calls", 0, 200
        )
    if "step_calls" in normalized:
        normalized["step_calls"] = _coerce_int_range(
            normalized["step_calls"], "context.step_calls", 1, 200
        )
    if "truncate_long_data" in normalized:
        normalized["truncate_long_data"] = _coerce_bool(
            normalized["truncate_long_data"], "context.truncate_long_data"
        )
    if "long_data_threshold" in normalized:
        normalized["long_data_threshold"] = _coerce_int_range(
            normalized["long_data_threshold"], "context.long_data_threshold", 200, 200000
        )
    if "long_data_head_chars" in normalized:
        normalized["long_data_head_chars"] = _coerce_int_range(
            normalized["long_data_head_chars"], "context.long_data_head_chars", 0, 200000
        )
    if "long_data_tail_chars" in normalized:
        normalized["long_data_tail_chars"] = _coerce_int_range(
            normalized["long_data_tail_chars"], "context.long_data_tail_chars", 0, 200000
        )

    start_pct = normalized.get("compress_start_pct")
    target_pct = normalized.get("compress_target_pct")
    if start_pct is not None and target_pct is not None and target_pct >= start_pct:
        raise ValueError("context.compress_target_pct must be less than context.compress_start_pct")

    keep_recent_calls = normalized.get("keep_recent_calls")
    step_calls = normalized.get("step_calls")
    if (
        keep_recent_calls is not None
        and step_calls is not None
        and keep_recent_calls > 0
        and step_calls > keep_recent_calls
    ):
        raise ValueError("context.step_calls must be <= context.keep_recent_calls when keep_recent_calls > 0")

    threshold = normalized.get("long_data_threshold")
    head_chars = normalized.get("long_data_head_chars")
    tail_chars = normalized.get("long_data_tail_chars")
    if threshold is not None and head_chars is not None and tail_chars is not None:
        if head_chars + tail_chars > threshold:
            raise ValueError("context.long_data_head_chars + context.long_data_tail_chars must be <= context.long_data_threshold")

    return normalized


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config)
    llm = dict(normalized.get("llm", {}))
    if "timeout_sec" in llm:
        llm["timeout_sec"] = _coerce_timeout(llm["timeout_sec"])
    normalized["llm"] = llm
    context = dict(normalized.get("context", {}))
    normalized["context"] = _normalize_context_config(context)
    agent = dict(normalized.get("agent", {}))
    if "react_max_iterations" in agent:
        agent["react_max_iterations"] = _coerce_react_max_iterations(agent["react_max_iterations"])
    normalized["agent"] = agent
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
