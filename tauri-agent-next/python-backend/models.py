from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


LLMApiFormat = Literal["openai_chat_completions", "openai_responses"]
LLMProfile = Literal["openai", "openai_compatible", "deepseek", "zhipu"]


class LLMConfig(BaseModel):
    id: Optional[str] = None
    name: str
    api_format: LLMApiFormat = "openai_chat_completions"
    api_profile: LLMProfile = "openai"
    api_key: str
    base_url: Optional[str] = None
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=32000)
    max_context_tokens: int = Field(default=200000, ge=1000, le=1000000)
    is_default: bool = False
    created_at: Optional[str] = None
    reasoning_effort: Optional[
        Literal["none", "minimal", "low", "medium", "high", "xhigh"]
    ] = "medium"
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "detailed"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    config_path: str
    runtime_data_dir: str


class DebugEmitRequest(BaseModel):
    stream: str = "run_event"
    topic: str = "debug.event"
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    done: bool = False
