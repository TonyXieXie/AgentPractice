import json
from typing import Any, Dict, Optional

from agent_team import AgentTeam
from tools.base import Tool, ToolParameter
from tools.context import get_tool_context


def _parse_json_input(input_data: str) -> Dict[str, Any]:
    if not input_data:
        return {}
    text = str(input_data).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class HandoffTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "handoff"
        self.description = "Transfer control to another agent in the team."
        self._current_profile: Optional[str] = None
        self._active_team_id: Optional[str] = None
        self._require_work_summary = False
        self.parameters = [
            ToolParameter(
                name="target_agent",
                type="string",
                description="Target agent profile id to hand off to.",
                required=True
            ),
            ToolParameter(
                name="reason",
                type="string",
                description="Short reason that will be written into the shared conversation history.",
                required=True
            ),
            ToolParameter(
                name="work_summary",
                type="string",
                description="Summary of the work already completed before handing off.",
                required=False
            ),
        ]

    def bind_context(self, context: Optional[Dict[str, Any]] = None) -> Optional["Tool"]:
        ctx = context or {}
        current_profile = str(
            ctx.get("current_agent_profile")
            or ctx.get("agent_profile")
            or ""
        ).strip()
        active_team_id = str(ctx.get("current_agent_team_id") or ctx.get("agent_team_id") or "").strip() or None
        app_config = ctx.get("app_config") if isinstance(ctx.get("app_config"), dict) else {}
        agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        team_cfg = agent_cfg.get("team", {}) if isinstance(agent_cfg, dict) else {}
        execution_mode = str(team_cfg.get("execution_mode") or "").strip().lower()
        team = AgentTeam(ctx.get("app_config") if isinstance(ctx.get("app_config"), dict) else None)
        self._current_profile = current_profile or None
        self._active_team_id = active_team_id
        self._require_work_summary = execution_mode == "multi_session"
        if current_profile:
            if not team.list_handoff_target_ids(current_profile, active_team_id=active_team_id):
                return None
            self.description = team.build_handoff_tool_description(current_profile, active_team_id=active_team_id)
            if self._require_work_summary:
                self.description += "\nInclude work_summary describing what you already completed before handing off."
        return self

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        tool_ctx = get_tool_context()
        current_profile = str(
            self._current_profile
            or tool_ctx.get("current_agent_profile")
            or tool_ctx.get("agent_profile")
            or ""
        ).strip()
        active_team_id = str(
            self._active_team_id
            or tool_ctx.get("current_agent_team_id")
            or tool_ctx.get("agent_team_id")
            or ""
        ).strip() or None
        target_agent = str(data.get("target_agent") or data.get("profile_id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        work_summary = str(data.get("work_summary") or "").strip()

        team = AgentTeam(tool_ctx.get("app_config") if isinstance(tool_ctx.get("app_config"), dict) else None)
        try:
            team.ensure_handoff_allowed(current_profile, target_agent, active_team_id=active_team_id)
        except Exception as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error": str(exc),
                    "from_agent": current_profile,
                    "target_agent": target_agent,
                    "reason": reason,
                    "work_summary": work_summary,
                },
                ensure_ascii=False
            )

        if self._require_work_summary and not work_summary:
            return json.dumps(
                {
                    "status": "error",
                    "error": "Missing required field: work_summary",
                    "from_agent": current_profile,
                    "target_agent": target_agent,
                    "reason": reason,
                    "work_summary": work_summary,
                },
                ensure_ascii=False
            )

        if not reason:
            reason = f"Transferred from {current_profile} to {target_agent}."

        return json.dumps(
            {
                "status": "handoff",
                "from_agent": current_profile,
                "target_agent": target_agent,
                "reason": reason,
                "work_summary": work_summary,
            },
            ensure_ascii=False
        )
