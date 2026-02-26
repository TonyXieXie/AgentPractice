import json
import os
import re
import hashlib
from typing import Any, Dict, List, Optional, Set

from app_config import get_app_config, update_app_config
from tools.base import ToolRegistry
from tools.mcp_tool import MCPTool
from mcp_client import list_tools_sync


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


_REGISTERED_MCP_TOOL_NAMES: Set[str] = set()


def _sanitize_tool_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def safe_mcp_tool_name(server_label: str, tool_name: str, existing: Optional[Set[str]] = None) -> str:
    existing = set(existing or [])
    base = f"mcp__{_sanitize_tool_segment(server_label, 'server')}__{_sanitize_tool_segment(tool_name, 'tool')}"
    name = base
    if name in existing:
        suffix = hashlib.sha1(f"{server_label}:{tool_name}".encode("utf-8")).hexdigest()[:6]
        name = f"{base}__{suffix}"
    counter = 1
    while name in existing:
        counter += 1
        name = f"{base}__{counter}"
    return name


def _build_runtime_server(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(server, dict):
        return None
    if server.get("enabled") is False:
        return None
    server_label = str(server.get("server_label") or "").strip()
    if not server_label:
        return None

    server_url = server.get("server_url")
    connector_id = server.get("connector_id")
    if connector_id:
        print(f"[MCP] connector_id is not supported for local tools: {server_label}")
        if not server_url:
            return None
    if not server_url:
        return None

    runtime: Dict[str, Any] = {
        "server_label": server_label,
        "server_url": server_url,
        "require_approval": server.get("require_approval"),
        "allowed_tools": server.get("allowed_tools")
    }

    auth_env = server.get("authorization_env")
    if isinstance(auth_env, str) and auth_env.strip():
        token = os.getenv(auth_env.strip())
        if token:
            runtime["authorization"] = token.strip()
        else:
            print(f"[MCP] authorization_env not set for {server_label}: {auth_env}")

    headers_env = server.get("headers_env")
    headers: Dict[str, str] = {}
    if isinstance(headers_env, str) and headers_env.strip():
        raw_headers = os.getenv(headers_env.strip())
        if raw_headers:
            parsed_headers = _parse_headers_env(raw_headers, server_label)
            if parsed_headers:
                headers.update(parsed_headers)
        else:
            print(f"[MCP] headers_env not set for {server_label}: {headers_env}")
    if runtime.get("authorization") and "Authorization" not in headers:
        headers["Authorization"] = runtime["authorization"]
    if headers:
        runtime["headers"] = headers

    return runtime


def _filter_tools_by_allowed(tools: List[Dict[str, Any]], allowed_cfg: Any) -> List[Dict[str, Any]]:
    if allowed_cfg is None:
        return tools
    if isinstance(allowed_cfg, list):
        allow_set = {str(item).strip() for item in allowed_cfg if str(item).strip()}
        return [tool for tool in tools if str(tool.get("name") or "").strip() in allow_set]
    if isinstance(allowed_cfg, dict):
        allow_set = None
        if isinstance(allowed_cfg.get("tool_names"), list):
            allow_set = {str(item).strip() for item in allowed_cfg.get("tool_names") if str(item).strip()}
        read_only = allowed_cfg.get("read_only")
        filtered = []
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if allow_set is not None and name not in allow_set:
                continue
            if read_only is not None:
                annotations = tool.get("annotations") or {}
                if isinstance(annotations, dict):
                    tool_read_only = bool(annotations.get("readOnly") or annotations.get("read_only"))
                else:
                    tool_read_only = False
                if bool(read_only) != tool_read_only:
                    continue
            filtered.append(tool)
        return filtered
    return tools


def register_mcp_tools_from_config() -> List[str]:
    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    mcp_cfg = agent_cfg.get("mcp", {}) if isinstance(agent_cfg, dict) else {}
    servers = mcp_cfg.get("servers", []) if isinstance(mcp_cfg, dict) else []
    if not isinstance(servers, list):
        return []

    existing_names = set(ToolRegistry.list_names())
    registered: List[str] = []

    for server in servers:
        runtime = _build_runtime_server(server)
        if not runtime:
            continue
        server_label = runtime.get("server_label") or "mcp"
        tools_meta: List[Dict[str, Any]] = []
        try:
            tools_meta = list_tools_sync(runtime)
        except Exception as exc:
            allowed = runtime.get("allowed_tools")
            allowed_names: List[str] = []
            if isinstance(allowed, list):
                allowed_names = [str(item).strip() for item in allowed if str(item).strip()]
            elif isinstance(allowed, dict) and isinstance(allowed.get("tool_names"), list):
                allowed_names = [str(item).strip() for item in allowed.get("tool_names") if str(item).strip()]
            if allowed_names:
                for name in allowed_names:
                    tools_meta.append({
                        "name": name,
                        "description": f"MCP tool from {server_label} (stub)",
                        "inputSchema": {"type": "object", "properties": {}},
                        "annotations": {}
                    })
                print(f"[MCP] tools/list failed for {server_label}, using allowed_tools as stub: {exc}")
            else:
                print(f"[MCP] tools/list failed for {server_label}: {exc}")
                continue

        tools_meta = _filter_tools_by_allowed(tools_meta, runtime.get("allowed_tools"))
        for tool_meta in tools_meta:
            tool_name = str(tool_meta.get("name") or "").strip()
            if not tool_name:
                continue
            safe_name = safe_mcp_tool_name(server_label, tool_name, existing_names)
            display_name = build_mcp_tool_name(server_label, tool_name)
            mcp_tool = MCPTool(runtime, tool_meta, safe_name, display_name=display_name)
            try:
                ToolRegistry.register(mcp_tool)
                existing_names.add(safe_name)
                registered.append(safe_name)
            except Exception as exc:
                print(f"[MCP] Failed to register tool {safe_name}: {exc}")

    _REGISTERED_MCP_TOOL_NAMES.update(registered)
    return registered


def refresh_mcp_tools() -> List[str]:
    for name in list(_REGISTERED_MCP_TOOL_NAMES):
        try:
            ToolRegistry.unregister(name)
        except Exception:
            pass
    _REGISTERED_MCP_TOOL_NAMES.clear()
    return register_mcp_tools_from_config()


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
