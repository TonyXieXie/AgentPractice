from typing import Optional

from fastapi import HTTPException

from models import ToolPermissionRequestUpdate
from repositories.permission_repository import list_permission_requests, update_permission_request
from .config_service import get_tools_config_route
from tools.config import update_tool_config
from tools.builtin.common import _extract_command_name


def get_tool_permissions(status: Optional[str] = None):
    return list_permission_requests(status=status)


def update_tool_permission(request_id: int, update: ToolPermissionRequestUpdate):
    updated = update_permission_request(request_id, update.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Permission request not found")
    if update.status == "approved" and updated.get("tool_name") == "run_shell":
        cmd_name = _extract_command_name(updated.get("path") or "")
        if cmd_name:
            cfg = get_tools_config_route()
            allowlist = list(cfg.get("shell", {}).get("allowlist", []) or [])
            allowset = {str(item).lower() for item in allowlist}
            if cmd_name.lower() not in allowset:
                allowlist.append(cmd_name)
                try:
                    update_tool_config({"shell": {"allowlist": allowlist}})
                except Exception:
                    pass
    return updated

