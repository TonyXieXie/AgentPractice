import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_paths import get_app_config_path as resolve_runtime_app_config_path


_DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "llm": {
        "timeout_sec": 180.0,
        "reasoning_summary": "detailed",
        "auto_title_enabled": True
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
        "ast_enabled": True,
        "subagent_profile": "subagent",
        "code_map": {
            "enabled": True,
            "max_symbols": 40,
            "max_files": 20,
            "max_lines": 30,
            "weight_refs": 1.0,
            "weight_mentions": 2.0
        },
        "mcp": {
            "servers": []
        },
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
                "id": "pty_status",
                "name": "Live PTY",
                "type": "workflow",
                "prompt": "Live PTY sessions for this chat:\n{{pty_sessions}}"
            },
            {
                "id": "code_map",
                "name": "Code Map",
                "type": "domain_knowledge",
                "prompt": "{{code_map_prompt}}"
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
            },
            {
                "id": "file_references",
                "name": "File References",
                "type": "output_format",
                "prompt": "File References: When referencing files in your response, make sure to include the relevant start line and always follow the below rules:\n- Use inline code to make file paths clickable.\n- Each reference should have a stand alone path. Even if it's the same file.\n- Accepted: absolute, workspace-relative, a/ or b/ diff prefixes, or bare filename/suffix.\n- Line/column (1-based, optional): :line[:column] or #Lline[Ccolumn] (column defaults to 1).\n- Do not use URIs like file://, vscode://, or https://.\n- Do not provide range of lines.\n- Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\\repo\\project\\main.rs:12:5"
            }
        ],
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "description": "Primary profile for the main assistant.",
                "abilities": ["tools_all", "rg_search", "apply_patch", "pty_status", "code_map", "tool_json", "output_concise", "file_references"],
                "spawnable": False
            },
            {
                "id": "subagent",
                "name": "Subagent",
                "description": "Spawnable profile for delegated tasks.",
                "abilities": ["tools_all", "rg_search", "apply_patch", "pty_status", "code_map", "tool_json", "output_concise", "file_references"],
                "spawnable": True
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


def _get_config_file_path() -> Path:
    global _CONFIG_PATH_OVERRIDE
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE
    return resolve_runtime_app_config_path()


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


def _coerce_reasoning_summary(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("llm.reasoning_summary must be a string")
    normalized = value.strip().lower()
    if normalized not in ("auto", "concise", "detailed"):
        raise ValueError("llm.reasoning_summary must be one of: auto, concise, detailed")
    return normalized


def _coerce_react_max_iterations(value: Any) -> int:
    try:
        max_iterations = int(value)
    except (TypeError, ValueError):
        raise ValueError("agent.react_max_iterations must be an integer")
    if max_iterations < 1:
        raise ValueError("agent.react_max_iterations must be >= 1")
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


def _coerce_int_min(value: Any, field: str, min_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if parsed < min_value:
        raise ValueError(f"{field} must be >= {min_value}")
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
        normalized["min_keep_messages"] = _coerce_int_min(
            normalized["min_keep_messages"], "context.min_keep_messages", 0
        )
    if "keep_recent_calls" in normalized:
        normalized["keep_recent_calls"] = _coerce_int_min(
            normalized["keep_recent_calls"], "context.keep_recent_calls", 0
        )
    if "step_calls" in normalized:
        normalized["step_calls"] = _coerce_int_min(
            normalized["step_calls"], "context.step_calls", 1
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
def _coerce_code_map(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("agent.code_map must be an object")
    result = dict(value)
    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])
    for key, maximum, minimum in (
        ("max_symbols", 200, 1),
        ("max_files", 100, 1),
        ("max_lines", 200, 1)
    ):
        if key in result:
            try:
                num = int(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"agent.code_map.{key} must be an integer")
            if num < minimum or num > maximum:
                raise ValueError(f"agent.code_map.{key} must be between {minimum} and {maximum}")
            result[key] = num
    for key in ("weight_refs", "weight_mentions"):
        if key in result:
            try:
                result[key] = float(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"agent.code_map.{key} must be a number")
    return result


def _normalize_mcp_filter(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    result: Dict[str, Any] = {}
    if "tool_names" in value:
        if not isinstance(value["tool_names"], list):
            raise ValueError(f"{field}.tool_names must be a list of strings")
        names = [
            str(item).strip()
            for item in value["tool_names"]
            if isinstance(item, (str, int, float))
        ]
        names = [name for name in names if name]
        result["tool_names"] = names
    if "read_only" in value:
        result["read_only"] = _coerce_bool(value["read_only"], f"{field}.read_only")
    return result


def _normalize_mcp_server(value: Any, index: int, labels: set) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"agent.mcp.servers[{index}] must be an object")
    server_label = str(value.get("server_label") or "").strip()
    if not server_label:
        raise ValueError(f"agent.mcp.servers[{index}].server_label is required")
    if server_label in labels:
        raise ValueError("agent.mcp.servers.server_label must be unique")
    labels.add(server_label)

    server_url = value.get("server_url")
    connector_id = value.get("connector_id")
    server_url = str(server_url).strip() if server_url is not None else None
    connector_id = str(connector_id).strip() if connector_id is not None else None
    if not server_url:
        server_url = None
    if not connector_id:
        connector_id = None
    if not server_url and not connector_id:
        raise ValueError(
            f"agent.mcp.servers[{index}] must set server_url (connector_id is ignored)"
        )

    enabled = True
    if "enabled" in value:
        enabled = _coerce_bool(value.get("enabled"), f"agent.mcp.servers[{index}].enabled")

    server_description = value.get("server_description")
    if server_description is not None and not isinstance(server_description, str):
        raise ValueError(f"agent.mcp.servers[{index}].server_description must be a string")
    server_description = server_description.strip() if isinstance(server_description, str) else None

    authorization_env = value.get("authorization_env")
    if authorization_env is not None and not isinstance(authorization_env, str):
        raise ValueError(f"agent.mcp.servers[{index}].authorization_env must be a string")
    authorization_env = authorization_env.strip() if isinstance(authorization_env, str) else None

    headers_env = value.get("headers_env")
    if headers_env is not None and not isinstance(headers_env, str):
        raise ValueError(f"agent.mcp.servers[{index}].headers_env must be a string")
    headers_env = headers_env.strip() if isinstance(headers_env, str) else None

    allowed_tools = value.get("allowed_tools")
    normalized_allowed: Any = None
    if allowed_tools is not None:
        if isinstance(allowed_tools, list):
            names = [
                str(item).strip()
                for item in allowed_tools
                if isinstance(item, (str, int, float))
            ]
            names = [name for name in names if name]
            normalized_allowed = names
        elif isinstance(allowed_tools, dict):
            normalized_allowed = _normalize_mcp_filter(
                allowed_tools, f"agent.mcp.servers[{index}].allowed_tools"
            )
        else:
            raise ValueError(f"agent.mcp.servers[{index}].allowed_tools must be a list or object")

    require_approval = value.get("require_approval")
    normalized_require: Any = None
    if require_approval is not None:
        if isinstance(require_approval, str):
            normalized = require_approval.strip().lower()
            if normalized not in ("always", "never"):
                raise ValueError(
                    f"agent.mcp.servers[{index}].require_approval must be always, never, or an object"
                )
            normalized_require = normalized
        elif isinstance(require_approval, dict):
            require_obj: Dict[str, Any] = {}
            for key in ("always", "never"):
                if key in require_approval and require_approval[key] is not None:
                    require_obj[key] = _normalize_mcp_filter(
                        require_approval[key],
                        f"agent.mcp.servers[{index}].require_approval.{key}"
                    )
            normalized_require = require_obj
        else:
            raise ValueError(
                f"agent.mcp.servers[{index}].require_approval must be always, never, or an object"
            )

    normalized: Dict[str, Any] = {
        "server_label": server_label,
        "enabled": enabled
    }
    if server_url:
        normalized["server_url"] = server_url
    if connector_id:
        normalized["connector_id"] = connector_id
    if server_description:
        normalized["server_description"] = server_description
    if authorization_env:
        normalized["authorization_env"] = authorization_env
    if headers_env:
        normalized["headers_env"] = headers_env
    if normalized_allowed is not None:
        normalized["allowed_tools"] = normalized_allowed
    if normalized_require is not None:
        normalized["require_approval"] = normalized_require
    return normalized


def _normalize_mcp_config(mcp: Any) -> Dict[str, Any]:
    if not isinstance(mcp, dict):
        raise ValueError("agent.mcp must be an object")
    servers = mcp.get("servers")
    if servers is None:
        servers = []
    if not isinstance(servers, list):
        raise ValueError("agent.mcp.servers must be a list")
    normalized_servers = []
    labels: set = set()
    for idx, server in enumerate(servers):
        normalized_servers.append(_normalize_mcp_server(server, idx, labels))
    return {"servers": normalized_servers}


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config)
    llm = dict(normalized.get("llm", {}))
    if "timeout_sec" in llm:
        llm["timeout_sec"] = _coerce_timeout(llm["timeout_sec"])
    if "reasoning_summary" in llm:
        llm["reasoning_summary"] = _coerce_reasoning_summary(llm["reasoning_summary"])
    if "auto_title_enabled" in llm:
        llm["auto_title_enabled"] = _coerce_bool(llm["auto_title_enabled"], "llm.auto_title_enabled")
    normalized["llm"] = llm
    context = dict(normalized.get("context", {}))
    normalized["context"] = _normalize_context_config(context)
    agent = dict(normalized.get("agent", {}))
    if "react_max_iterations" in agent:
        agent["react_max_iterations"] = _coerce_react_max_iterations(agent["react_max_iterations"])
    if "ast_enabled" in agent:
        agent["ast_enabled"] = _coerce_bool(agent["ast_enabled"], "agent.ast_enabled")
    if "subagent_profile" in agent:
        if not isinstance(agent["subagent_profile"], str):
            raise ValueError("agent.subagent_profile must be a string")
        agent["subagent_profile"] = agent["subagent_profile"].strip()
    if "code_map" in agent:
        agent["code_map"] = _coerce_code_map(agent["code_map"])
    if "mcp" in agent:
        agent["mcp"] = _normalize_mcp_config(agent["mcp"])
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
    if not isinstance(patch, dict):
        raise ValueError("Config update must be a JSON object.")
    current_file = _load_config_file()
    merged_file = _deep_merge(current_file, patch)
    merged_file = _normalize_config(merged_file)
    path = _get_config_file_path()
    content = json.dumps(merged_file, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _APP_CONFIG = _load_config()
    return _APP_CONFIG
