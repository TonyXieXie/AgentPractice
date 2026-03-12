from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class AgentProfile(BaseModel):
    id: str
    agent_type: str = "assistant"
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    tool_policy_text: Optional[str] = None
    allowed_tool_names: Optional[list[str]] = None
    extends: Optional[str] = None
    editable: bool = True
    subscribed_topics: list[str] = Field(default_factory=list)
    executable_event_topics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("description", "system_prompt", "tool_policy_text", "extends", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @field_validator("allowed_tool_names", mode="before")
    @classmethod
    def _normalize_allowed_tool_names(cls, value: Any) -> Optional[list[str]]:
        normalized = cls._normalize_string_list(value)
        return normalized or None

    @field_validator("subscribed_topics", mode="before")
    @classmethod
    def _normalize_subscribed_topics(cls, value: Any) -> list[str]:
        return cls._normalize_string_list(value)

    @field_validator("executable_event_topics", mode="before")
    @classmethod
    def _normalize_event_topics(cls, value: Any) -> list[str]:
        return cls._normalize_string_list(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def can_execute_event(self, topic: str) -> bool:
        normalized_topic = str(topic or "").strip()
        return bool(normalized_topic and normalized_topic in self.executable_event_topics)

    def subscribes_to(self, topic: str) -> bool:
        normalized_topic = str(topic or "").strip()
        return bool(normalized_topic and normalized_topic in self.subscribed_topics)

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized
