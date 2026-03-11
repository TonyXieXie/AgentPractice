from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Sequence

from agents.execution.prompt_ir import PromptIR
from tools import Tool


@dataclass(slots=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str = ""
    index: int = 0


@dataclass(slots=True)
class ProviderToolResult:
    tool_call_id: str
    tool_name: str
    content: str
    ok: bool


@dataclass(slots=True)
class ProviderTurnEvent:
    event_type: str
    delta: str = ""
    tool_call: ProviderToolCall | None = None
    tool_calls: List[ProviderToolCall] = field(default_factory=list)


class ProviderAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def supports(self, llm_client: Any) -> bool:
        """Return whether this adapter can drive the supplied LLM client."""

    @abstractmethod
    def prepare_request_overrides(
        self,
        *,
        request_overrides: Dict[str, Any],
        tools: Iterable[Tool],
        llm_client: Any,
    ) -> Dict[str, Any]:
        """Attach provider-specific request payload such as tool schemas."""

    @abstractmethod
    async def run_turn(
        self,
        *,
        prompt_ir: PromptIR,
        llm_client: Any,
        request_overrides: Dict[str, Any],
    ) -> AsyncGenerator[ProviderTurnEvent, None]:
        """Normalize provider streaming output into strategy-agnostic events."""

    @abstractmethod
    def append_tool_results(
        self,
        *,
        prompt_ir: PromptIR,
        assistant_content: str,
        tool_calls: Sequence[ProviderToolCall],
        tool_results: Sequence[ProviderToolResult],
    ) -> None:
        """Append provider-specific follow-up messages after tool execution."""
