from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Optional

from agents.profile import AgentProfile
from app_config import get_app_config


ConfigLoader = Callable[[], Dict[str, Any]]


class AgentProfileRepository:
    def __init__(self, config_loader: ConfigLoader = get_app_config) -> None:
        self._config_loader = config_loader

    @property
    def default_profile_id(self) -> str:
        config = self._config_loader()
        agent_cfg = config.get("agent") if isinstance(config, dict) else {}
        default_profile = ""
        if isinstance(agent_cfg, dict):
            default_profile = str(agent_cfg.get("default_profile") or "").strip()
        return default_profile or "default"

    async def get(self, profile_id: str) -> Optional[AgentProfile]:
        normalized = str(profile_id or "").strip()
        if not normalized:
            return None
        return self._load_profiles().get(normalized)

    async def get_required(self, profile_id: str) -> AgentProfile:
        profile = await self.get(profile_id)
        if profile is None:
            raise ValueError(f"Agent profile not found: {profile_id}")
        return profile

    async def list_all(self) -> list[AgentProfile]:
        return list(self._load_profiles().values())

    def _load_profiles(self) -> dict[str, AgentProfile]:
        config = self._config_loader()
        agent_cfg = config.get("agent") if isinstance(config, dict) else {}
        raw_profiles: Any = {}
        if isinstance(agent_cfg, dict):
            raw_profiles = agent_cfg.get("profiles") or {}

        loaded: dict[str, AgentProfile] = {}
        if isinstance(raw_profiles, dict):
            for key, value in raw_profiles.items():
                profile = self._build_profile(profile_id=str(key), raw_value=value)
                if profile is not None:
                    loaded[profile.id] = profile
            return loaded

        if isinstance(raw_profiles, list):
            for item in raw_profiles:
                if not isinstance(item, dict):
                    continue
                profile = self._build_profile(profile_id=str(item.get("id") or ""), raw_value=item)
                if profile is not None:
                    loaded[profile.id] = profile
        return loaded

    def _build_profile(self, *, profile_id: str, raw_value: Any) -> Optional[AgentProfile]:
        normalized_id = str(profile_id or "").strip()
        if not normalized_id:
            return None
        payload = deepcopy(raw_value) if isinstance(raw_value, dict) else {}
        payload["id"] = normalized_id
        try:
            return AgentProfile.model_validate(payload)
        except Exception:
            return None
