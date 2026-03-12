from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Optional

from agents.profile import AgentProfile
from app_config import get_app_config, get_default_app_config, get_runtime_app_config


ConfigLoader = Callable[[], Dict[str, Any]]


class AgentProfileRepository:
    def __init__(
        self,
        config_loader: ConfigLoader = get_app_config,
        *,
        default_config_loader: ConfigLoader = get_default_app_config,
        runtime_config_loader: ConfigLoader = get_runtime_app_config,
    ) -> None:
        self._config_loader = config_loader
        self._default_config_loader = default_config_loader
        self._runtime_config_loader = runtime_config_loader

    @property
    def default_profile_id(self) -> str:
        _, default_profile_id = self._load_profile_bundle()
        return default_profile_id

    async def get(self, profile_id: str) -> Optional[AgentProfile]:
        normalized = str(profile_id or "").strip()
        if not normalized:
            return None
        profiles, _ = self._load_profile_bundle()
        return profiles.get(normalized)

    async def get_required(self, profile_id: str) -> AgentProfile:
        profile = await self.get(profile_id)
        if profile is None:
            raise ValueError(f"Agent profile not found: {profile_id}")
        return profile

    async def list_all(self) -> list[AgentProfile]:
        profiles, _ = self._load_profile_bundle()
        return list(profiles.values())

    def _load_profile_bundle(self) -> tuple[dict[str, AgentProfile], str]:
        merged_config = self._config_loader()
        default_config = self._default_config_loader()
        runtime_config = self._runtime_config_loader()

        default_profile_id = self._resolve_default_profile_id(merged_config)
        builtin_profiles = self._normalize_raw_profiles(
            self._extract_raw_profiles(default_config),
            source="default",
        )
        runtime_profiles = self._normalize_raw_profiles(
            self._extract_raw_profiles(runtime_config),
            source="runtime",
        )
        collisions = set(builtin_profiles) & set(runtime_profiles)
        if collisions:
            joined = ", ".join(sorted(collisions))
            raise ValueError(
                f"Runtime agent profiles must not override built-in profiles: {joined}"
            )

        raw_profiles: dict[str, dict[str, Any]] = {}
        for profile_id, payload in builtin_profiles.items():
            normalized_payload = deepcopy(payload)
            normalized_payload["id"] = profile_id
            normalized_payload.setdefault("editable", False)
            raw_profiles[profile_id] = normalized_payload
        for profile_id, payload in runtime_profiles.items():
            normalized_payload = deepcopy(payload)
            normalized_payload["id"] = profile_id
            normalized_payload.setdefault("editable", True)
            raw_profiles[profile_id] = normalized_payload

        resolved: dict[str, AgentProfile] = {}
        resolving: list[str] = []
        for profile_id in raw_profiles:
            resolved[profile_id] = self._resolve_profile(
                profile_id,
                raw_profiles=raw_profiles,
                resolved=resolved,
                resolving=resolving,
            )

        if default_profile_id not in resolved:
            raise ValueError(f"Default agent profile not found: {default_profile_id}")
        return resolved, default_profile_id

    def _resolve_profile(
        self,
        profile_id: str,
        *,
        raw_profiles: dict[str, dict[str, Any]],
        resolved: dict[str, AgentProfile],
        resolving: list[str],
    ) -> AgentProfile:
        existing = resolved.get(profile_id)
        if existing is not None:
            return existing
        raw_payload = raw_profiles.get(profile_id)
        if raw_payload is None:
            raise ValueError(f"Agent profile not found: {profile_id}")
        if profile_id in resolving:
            cycle = " -> ".join([*resolving, profile_id])
            raise ValueError(f"Agent profile inheritance cycle detected: {cycle}")

        resolving.append(profile_id)
        try:
            payload = deepcopy(raw_payload)
            base_profile_id = str(payload.get("extends") or "").strip()
            if base_profile_id:
                if base_profile_id not in raw_profiles:
                    raise ValueError(
                        f"Base agent profile not found: {base_profile_id} (referenced by {profile_id})"
                    )
                base_profile = self._resolve_profile(
                    base_profile_id,
                    raw_profiles=raw_profiles,
                    resolved=resolved,
                    resolving=resolving,
                )
                payload = _deep_merge(base_profile.model_dump(mode="python"), payload)
                payload["id"] = profile_id
            try:
                profile = AgentProfile.model_validate(payload)
            except Exception as exc:  # pragma: no cover - message normalization tested via ValueError
                raise ValueError(f"Invalid agent profile '{profile_id}': {exc}") from exc
            resolved[profile_id] = profile
            return profile
        finally:
            resolving.pop()

    def _resolve_default_profile_id(self, config: Dict[str, Any]) -> str:
        agent_cfg = config.get("agent") if isinstance(config, dict) else {}
        default_profile = ""
        if isinstance(agent_cfg, dict):
            default_profile = str(agent_cfg.get("default_profile") or "").strip()
        return default_profile or "default"

    def _extract_raw_profiles(self, config: Dict[str, Any]) -> Any:
        if not isinstance(config, dict):
            return {}
        agent_cfg = config.get("agent")
        if not isinstance(agent_cfg, dict):
            return {}
        return agent_cfg.get("profiles") or {}

    def _normalize_raw_profiles(
        self,
        raw_profiles: Any,
        *,
        source: str,
    ) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(raw_profiles, dict):
            iterable = raw_profiles.items()
        elif isinstance(raw_profiles, list):
            iterable = []
            for item in raw_profiles:
                if not isinstance(item, dict):
                    raise ValueError(f"Agent profiles from {source} config must be objects")
                iterable.append((item.get("id"), item))
        else:
            raise ValueError(f"Agent profiles from {source} config must be a dict or list")

        for raw_id, raw_value in iterable:
            profile_id = str(raw_id or "").strip()
            if not profile_id:
                raise ValueError("Agent profile id must be a non-empty string")
            if profile_id in normalized:
                raise ValueError(f"Duplicate agent profile id: {profile_id}")
            if not isinstance(raw_value, dict):
                raise ValueError(f"Agent profile '{profile_id}' must be an object")
            normalized[profile_id] = deepcopy(raw_value)
        return normalized


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
