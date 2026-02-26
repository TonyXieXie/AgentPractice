import json
import os
from typing import Any, Dict, List, Optional

from app_config import get_app_config, update_app_config


def _parse_headers_env(raw: str, label: str) -> Optional[Dict[str, str]]:
    try:
        data = json.loads(raw)
    except Exception:
        print(f"[MCP] Invalid headers_env JSON for {label}.")
        return None
    if not isinstance(data, dict):
        print(f"[MCP] headers_env must be a JSON object for {label}.")
        return None
    headers: Dict[str, str] = {}
    for key, value in data.items():
        if key is None:
            continue
        key_text = str(key).strip()
        if not key_text:
            continue
        headers[key_text] = str(value) if value is not None else ""
    return headers or None


def build_mcp_tools(app_config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if app_config is None:
        app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    mcp_cfg = agent_cfg.get("mcp", {}) if isinstance(agent_cfg, dict) else {}
    servers = mcp_cfg.get("servers", []) if isinstance(mcp_cfg, dict) else []
    if not isinstance(servers, list):
        return []

    tools: List[Dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        if server.get("enabled") is False:
            continue
        server_label = str(server.get("server_label") or "").strip()
        if not server_label:
            continue

        server_url = server.get("server_url")
        connector_id = server.get("connector_id")
        if not server_url and not connector_id:
            continue

        tool: Dict[str, Any] = {"type": "mcp", "server_label": server_label}
        if server_url:
            tool["server_url"] = server_url
        if connector_id:
            tool["connector_id"] = connector_id

        server_description = server.get("server_description")
        if isinstance(server_description, str) and server_description.strip():
            tool["server_description"] = server_description.strip()

        allowed_tools = server.get("allowed_tools")
        if allowed_tools is not None:
            tool["allowed_tools"] = allowed_tools

        require_approval = server.get("require_approval")
        if require_approval is not None:
            tool["require_approval"] = require_approval

        auth_env = server.get("authorization_env")
        if isinstance(auth_env, str) and auth_env.strip():
            token = os.getenv(auth_env.strip())
            if token:
                tool["authorization"] = token.strip()
            else:
                print(f"[MCP] authorization_env not set for {server_label}: {auth_env}")

        headers_env = server.get("headers_env")
        if isinstance(headers_env, str) and headers_env.strip():
            raw_headers = os.getenv(headers_env.strip())
            if raw_headers:
                headers = _parse_headers_env(raw_headers, server_label)
                if headers:
                    tool["headers"] = headers
            else:
                print(f"[MCP] headers_env not set for {server_label}: {headers_env}")

        tools.append(tool)

    return tools


def build_mcp_tool_name(server_label: str, tool_name: str) -> str:
    return f"mcp:{server_label}/{tool_name}"


def persist_mcp_tool_approval(server_label: str, tool_name: str) -> bool:
    app_config = get_app_config()
    if not isinstance(app_config, dict):
        return False
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config.get("agent"), dict) else {}
    mcp_cfg = agent_cfg.get("mcp", {}) if isinstance(agent_cfg, dict) else {}
    servers = mcp_cfg.get("servers", []) if isinstance(mcp_cfg, dict) else []
    if not isinstance(servers, list):
        return False

    updated_servers: List[Dict[str, Any]] = []
    updated = False
    for server in servers:
        if not isinstance(server, dict):
            continue
        if server.get("server_label") != server_label:
            updated_servers.append(server)
            continue

        next_server = dict(server)
        require_approval = next_server.get("require_approval")
        if require_approval == "never":
            updated_servers.append(next_server)
            continue

        require_obj: Dict[str, Any] = {}
        if isinstance(require_approval, dict):
            require_obj.update(require_approval)

        never_filter = require_obj.get("never")
        never_obj: Dict[str, Any] = dict(never_filter) if isinstance(never_filter, dict) else {}
        tool_names = never_obj.get("tool_names")
        names: List[str] = []
        if isinstance(tool_names, list):
            names = [str(item).strip() for item in tool_names if str(item).strip()]
        if tool_name not in names:
            names.append(tool_name)
        never_obj["tool_names"] = names
        require_obj["never"] = never_obj
        next_server["require_approval"] = require_obj
        updated = True
        updated_servers.append(next_server)

    if not updated:
        return False
    try:
        update_app_config({"agent": {"mcp": {"servers": updated_servers}}})
        return True
    except Exception as exc:
        print(f"[MCP] Failed to persist approval: {exc}")
        return False
