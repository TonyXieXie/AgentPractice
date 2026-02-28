from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import db


class AgentInstanceManager:
    def __init__(self) -> None:
        pass

    def _iter_profiles(self, agent_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        profiles = agent_config.get("profiles") if isinstance(agent_config, dict) else None
        if not isinstance(profiles, list):
            return []
        return [item for item in profiles if isinstance(item, dict) and str(item.get("id") or "").strip()]

    def ensure_session_instances(self, session_id: str, agent_config: Dict[str, Any]) -> List[Any]:
        instances: List[Any] = []
        for profile in self._iter_profiles(agent_config):
            profile_id = str(profile.get("id") or "").strip()
            if not profile_id:
                continue
            name = str(profile.get("name") or profile_id).strip() or profile_id
            abilities = profile.get("abilities") if isinstance(profile.get("abilities"), list) else []
            metadata = {
                "description": profile.get("description"),
                "spawnable": bool(profile.get("spawnable", False))
            }
            instance = db.upsert_agent_instance(
                session_id=session_id,
                profile_id=profile_id,
                name=name,
                abilities=[str(item) for item in abilities if str(item).strip()],
                metadata=metadata,
                status="active"
            )
            instances.append(instance)
        return instances

    def resolve_instance(
        self,
        *,
        session_id: str,
        target_instance_id: Optional[str] = None,
        target_profile_id: Optional[str] = None,
        required_abilities: Optional[List[str]] = None,
        agent_config: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        required_set = {str(item).strip() for item in (required_abilities or []) if str(item).strip()}

        if target_instance_id:
            instance = db.get_agent_instance(target_instance_id)
            if not instance or instance.session_id != session_id:
                return None
            return instance

        if agent_config:
            self.ensure_session_instances(session_id, agent_config)

        instances = db.list_agent_instances(session_id=session_id, status="active")
        if target_profile_id:
            for instance in instances:
                if str(instance.profile_id) == str(target_profile_id):
                    if required_set and not required_set.issubset(set(instance.abilities or [])):
                        continue
                    return instance
            return None

        for instance in instances:
            ability_set = set(instance.abilities or [])
            if required_set and not required_set.issubset(ability_set):
                continue
            return instance
        return None


_INSTANCE_MANAGER = AgentInstanceManager()


def get_agent_instance_manager() -> AgentInstanceManager:
    return _INSTANCE_MANAGER
