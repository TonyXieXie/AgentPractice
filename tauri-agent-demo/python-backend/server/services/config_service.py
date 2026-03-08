import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Query

from agents.prompt_builder import build_agent_prompt_and_tools
from app_config import get_app_config, update_app_config
from mcp_tools import refresh_mcp_tools
from models import LLMConfig, LLMConfigCreate, LLMConfigUpdate
from repositories import config_repository
from skills import list_skills
from ..agent_prompt_support import append_reasoning_summary_prompt, build_live_pty_prompt
from tools.base import ToolRegistry
from tools.builtin import register_builtin_tools
from tools.config import get_tool_config, update_tool_config


logger = logging.getLogger(__name__)



def _mcp_servers_signature(config: Any) -> str:
    if not isinstance(config, dict):
        return ""
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return ""
    mcp = agent.get("mcp")
    if not isinstance(mcp, dict):
        return ""
    servers = mcp.get("servers")
    if not isinstance(servers, list):
        return ""
    try:
        return json.dumps(servers, ensure_ascii=False, sort_keys=True)
    except Exception:
        return ""


def get_configs():
    return config_repository.list_configs()


def get_default_config():
    config = config_repository.get_default_config()
    if config:
        return config
    configs = config_repository.list_configs()
    if configs:
        return configs[0]
    raise HTTPException(status_code=404, detail="No config available")


def get_config(config_id: str):
    config = config_repository.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config


def create_config(config: LLMConfigCreate):
    try:
        return config_repository.create_config(config)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Create config failed", extra={"config_name": getattr(config, "name", None)})
        raise HTTPException(status_code=500, detail=f"Create config failed: {exc}")


def update_config(config_id: str, update: LLMConfigUpdate):
    try:
        config = config_repository.update_config(config_id, update)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Update config failed", extra={"config_id": config_id})
        raise HTTPException(status_code=500, detail=f"Update config failed: {exc}")
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config


def delete_config(config_id: str):
    if config_repository.config_in_use(config_id):
        raise HTTPException(status_code=400, detail="Config is in use by sessions")
    if config_repository.delete_config(config_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Config not found")


def get_app_config_route():
    return get_app_config()


def set_app_config(payload: Dict[str, Any]):
    try:
        before_sig = _mcp_servers_signature(get_app_config())
        updated = update_app_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Set app config failed")
        raise HTTPException(status_code=500, detail=f"Set app config failed: {exc}")
    after_sig = _mcp_servers_signature(updated)
    if before_sig != after_sig:
        try:
            refresh_mcp_tools()
        except Exception as exc:
            print(f"[MCP] Failed to refresh MCP tools: {exc}")
    return updated


def get_agent_prompt_route(
    profile_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    include_tools: Optional[bool] = Query(None),
    agent_type: Optional[str] = Query(None),
):
    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    llm_app_config = app_config.get("llm", {}) if isinstance(app_config, dict) else {}
    global_reasoning_summary = llm_app_config.get("reasoning_summary")

    include = True if include_tools is None else bool(include_tools)
    if agent_type is not None:
        include = str(agent_type).lower() != "simple"

    pty_prompt = build_live_pty_prompt(session_id)
    system_prompt, tools, resolved_profile_id, _ = build_agent_prompt_and_tools(
        profile_id,
        ToolRegistry.get_all(),
        include_tools=include,
        extra_context={"pty_sessions": pty_prompt},
        exclude_ability_ids=["code_map"],
    )
    system_prompt = append_reasoning_summary_prompt(system_prompt, global_reasoning_summary)

    profile_name = None
    profiles = agent_cfg.get("profiles") if isinstance(agent_cfg, dict) else None
    if isinstance(profiles, list) and resolved_profile_id:
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == resolved_profile_id:
                profile_name = profile.get("name") or resolved_profile_id
                break

    return {
        "prompt": system_prompt,
        "profile_id": resolved_profile_id,
        "profile_name": profile_name,
        "include_tools": include,
        "tool_names": [tool.name for tool in tools],
    }


def refresh_mcp_tools_route():
    try:
        registered = refresh_mcp_tools()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to refresh MCP tools: {exc}")
    return {"ok": True, "registered": registered}


def get_skills_route():
    return list_skills()


def get_tools_route():
    return [tool.to_dict() for tool in ToolRegistry.get_all()]


def get_tools_config_route():
    return get_tool_config()


def set_tools_config_route(payload: Dict[str, Any]):
    try:
        updated = update_tool_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ToolRegistry.clear()
    register_builtin_tools()
    return updated
