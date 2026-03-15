from typing import Any, Dict, List, Optional, Tuple

from app_config import get_app_config
from models import ChatMessageCreate, ChatSessionUpdate
from repositories import chat_repository, session_repository
from ws_hub import get_ws_hub


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class AgentTeam:
    def __init__(self, app_config: Optional[Dict[str, Any]] = None):
        self.app_config = app_config if isinstance(app_config, dict) else get_app_config()
        agent_cfg = self.app_config.get("agent", {}) if isinstance(self.app_config, dict) else {}
        self.agent_cfg = agent_cfg if isinstance(agent_cfg, dict) else {}

        profiles = self.agent_cfg.get("profiles") or []
        self.profiles = [profile for profile in profiles if isinstance(profile, dict)]
        self.profile_ids = {
            _normalize_text(profile.get("id")): profile
            for profile in self.profiles
            if _normalize_text(profile.get("id"))
        }

        legacy_team_cfg = self.agent_cfg.get("team") or {}
        self.legacy_team_cfg = legacy_team_cfg if isinstance(legacy_team_cfg, dict) else {}
        members = self.legacy_team_cfg.get("members") or []
        self.legacy_members = [member for member in members if isinstance(member, dict)]
        self.legacy_member_by_profile: Dict[str, Dict[str, Any]] = {}
        for member in self.legacy_members:
            profile_id = _normalize_text(member.get("profile_id"))
            if profile_id:
                self.legacy_member_by_profile[profile_id] = member

        selectable_teams = self.agent_cfg.get("teams") or []
        self.selectable_teams = [team for team in selectable_teams if isinstance(team, dict)]
        self.selectable_team_by_id: Dict[str, Dict[str, Any]] = {}
        for team in self.selectable_teams:
            team_id = _normalize_text(team.get("id"))
            if team_id:
                self.selectable_team_by_id[team_id] = team

    def get_profile(self, profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
        profile_key = _normalize_text(profile_id)
        if not profile_key:
            return None
        return self.profile_ids.get(profile_key)

    def get_selectable_team(self, team_id: Optional[str]) -> Optional[Dict[str, Any]]:
        team_key = _normalize_text(team_id)
        if not team_key:
            return None
        return self.selectable_team_by_id.get(team_key)

    def get_selectable_teams(self) -> List[Dict[str, Any]]:
        return list(self.selectable_teams)

    def get_team_member_ids(self, team_id: Optional[str]) -> List[str]:
        team = self.get_selectable_team(team_id)
        if not team:
            return []
        raw_members = team.get("member_profile_ids") or []
        if not isinstance(raw_members, list):
            return []
        members: List[str] = []
        for item in raw_members:
            profile_id = _normalize_text(item)
            if not profile_id or profile_id not in self.profile_ids:
                continue
            if profile_id not in members:
                members.append(profile_id)
        return members

    def get_team_leader_id(self, team_id: Optional[str]) -> Optional[str]:
        team = self.get_selectable_team(team_id)
        if not team:
            return None
        leader_profile_id = _normalize_text(team.get("leader_profile_id"))
        if leader_profile_id and leader_profile_id in self.profile_ids:
            return leader_profile_id
        members = self.get_team_member_ids(team_id)
        return members[0] if members else None

    def resolve_direct_agent(
        self,
        session_profile: Optional[str] = None,
        requested_profile: Optional[str] = None
    ) -> Optional[str]:
        requested = _normalize_text(requested_profile)
        if requested and requested in self.profile_ids:
            return requested

        current = _normalize_text(session_profile)
        if current and current in self.profile_ids:
            return current

        default_agent = _normalize_text(self.legacy_team_cfg.get("default_agent"))
        if default_agent and default_agent in self.profile_ids:
            return default_agent

        default_profile = _normalize_text(self.agent_cfg.get("default_profile"))
        if default_profile and default_profile in self.profile_ids:
            return default_profile

        for profile_id in self.profile_ids.keys():
            return profile_id
        return None

    def resolve_session_selection(
        self,
        session_profile: Optional[str] = None,
        session_team_id: Optional[str] = None,
        requested_profile: Optional[str] = None,
        requested_team_id: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        requested_profile_id = _normalize_text(requested_profile)
        requested_team_key = _normalize_text(requested_team_id)
        if requested_profile_id and requested_team_key:
            raise ValueError("agent_profile and agent_team_id cannot both be set")

        if requested_team_key:
            team = self.get_selectable_team(requested_team_key)
            if not team:
                raise ValueError(f"Unknown team: {requested_team_key}")
            current_team_key = _normalize_text(session_team_id)
            leader_profile_id = self.get_team_leader_id(requested_team_key)
            current_profile_id = _normalize_text(session_profile)
            member_ids = self.get_team_member_ids(requested_team_key)
            if current_team_key == requested_team_key and current_profile_id in member_ids:
                active_profile = current_profile_id
            else:
                active_profile = leader_profile_id
            return {
                "agent_profile": active_profile,
                "agent_team_id": requested_team_key,
            }

        stored_team_key = _normalize_text(session_team_id)
        if stored_team_key:
            team = self.get_selectable_team(stored_team_key)
            if team:
                member_ids = self.get_team_member_ids(stored_team_key)
                current_profile_id = _normalize_text(session_profile)
                active_profile = current_profile_id if current_profile_id in member_ids else self.get_team_leader_id(stored_team_key)
                return {
                    "agent_profile": active_profile,
                    "agent_team_id": stored_team_key,
                }

        return {
            "agent_profile": self.resolve_direct_agent(session_profile=session_profile, requested_profile=requested_profile_id),
            "agent_team_id": None,
        }

    def resolve_active_agent(
        self,
        session_profile: Optional[str] = None,
        requested_profile: Optional[str] = None,
        session_team_id: Optional[str] = None,
        requested_team_id: Optional[str] = None
    ) -> Optional[str]:
        selection = self.resolve_session_selection(
            session_profile=session_profile,
            session_team_id=session_team_id,
            requested_profile=requested_profile,
            requested_team_id=requested_team_id,
        )
        return _normalize_text(selection.get("agent_profile")) or None

    def list_handoff_target_ids(
        self,
        current_profile: Optional[str],
        active_team_id: Optional[str] = None
    ) -> List[str]:
        current = _normalize_text(current_profile)
        if not current:
            return []

        active_team_key = _normalize_text(active_team_id)
        if active_team_key:
            member_ids = self.get_team_member_ids(active_team_key)
            if not member_ids:
                return []
            return [profile_id for profile_id in member_ids if profile_id != current]

        member = self.legacy_member_by_profile.get(current)
        if not member:
            return []
        raw_targets = member.get("handoff_to") or []
        if not isinstance(raw_targets, list):
            return []
        targets: List[str] = []
        for item in raw_targets:
            target = _normalize_text(item)
            if not target or target == current or target not in self.profile_ids:
                continue
            if target not in targets:
                targets.append(target)
        return targets

    def list_handoff_targets(
        self,
        current_profile: Optional[str],
        active_team_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return [
            self.profile_ids[target]
            for target in self.list_handoff_target_ids(current_profile, active_team_id=active_team_id)
            if target in self.profile_ids
        ]

    def can_handoff(
        self,
        current_profile: Optional[str],
        target_profile: Optional[str],
        active_team_id: Optional[str] = None
    ) -> bool:
        target = _normalize_text(target_profile)
        if not target:
            return False
        return target in self.list_handoff_target_ids(current_profile, active_team_id=active_team_id)

    def ensure_handoff_allowed(
        self,
        current_profile: Optional[str],
        target_profile: Optional[str],
        active_team_id: Optional[str] = None
    ) -> None:
        current = _normalize_text(current_profile)
        target = _normalize_text(target_profile)
        if not current:
            raise ValueError("Missing current agent profile")
        if not target:
            raise ValueError("Missing target_agent")
        if target == current:
            raise ValueError("Cannot handoff to the same agent")
        if target not in self.profile_ids:
            raise ValueError(f"Unknown target agent: {target}")
        allowed = self.list_handoff_target_ids(current, active_team_id=active_team_id)
        if target not in allowed:
            choices = ", ".join(allowed) if allowed else "(none)"
            raise ValueError(f"Handoff not allowed: {current} -> {target}. Allowed targets: {choices}")

    def build_handoff_tool_description(
        self,
        current_profile: Optional[str],
        active_team_id: Optional[str] = None
    ) -> str:
        current = _normalize_text(current_profile)
        targets = self.list_handoff_targets(current, active_team_id=active_team_id)
        team = self.get_selectable_team(active_team_id)
        if not current or not targets:
            if team:
                team_name = _normalize_text(team.get("name") or team.get("id"))
                return (
                    "Transfer control to another agent in the selected team. "
                    f"No handoff targets are currently available for this agent in {team_name}."
                )
            return (
                "Transfer control to another agent in the team. "
                "No handoff targets are currently available for this agent."
            )

        lines = []
        if team:
            team_name = _normalize_text(team.get("name") or team.get("id"))
            lines.append("Transfer control to another agent in the selected team.")
            lines.append(f"Selected team: {team_name}")
        else:
            lines.append("Transfer control to another agent in the team.")
        lines.append(f"Current agent: {current}")
        lines.append("Allowed target agents:")
        for target in targets:
            profile_id = _normalize_text(target.get("id"))
            name = _normalize_text(target.get("name"))
            description = _normalize_text(target.get("description"))
            label = f"{profile_id} ({name})" if name and name != profile_id else profile_id
            if description:
                lines.append(f"- {label}: {description}")
            else:
                lines.append(f"- {label}")
        lines.append("Use a short reason that can be shown in the shared conversation history.")
        return "\n".join(lines)

    def build_handoff_message(self, from_agent: str, to_agent: str, reason: str) -> Tuple[str, Dict[str, Any]]:
        safe_reason = _normalize_text(reason) or "No reason provided."
        content = f"Handoff: {from_agent} -> {to_agent}\nReason: {safe_reason}"
        metadata = {
            "event_type": "handoff",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": safe_reason,
        }
        return content, metadata

    async def apply_handoff(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        reason: str,
        active_team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        self.ensure_handoff_allowed(from_agent, to_agent, active_team_id=active_team_id)
        content, metadata = self.build_handoff_message(from_agent, to_agent, reason)
        message = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session_id,
                role="system",
                content=content,
                metadata=metadata,
            )
        )
        session_repository.update_session(session_id, ChatSessionUpdate(agent_profile=to_agent))

        payload = {
            "type": "session_message",
            "session_id": session_id,
            "message": message.dict(),
            "active_agent_profile": to_agent,
        }
        try:
            await get_ws_hub().emit(session_id, payload)
        except Exception:
            pass
        return payload
