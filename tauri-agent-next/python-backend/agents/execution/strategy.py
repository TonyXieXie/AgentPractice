from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, Optional

if TYPE_CHECKING:
    from agents.message import AgentMessage
    from agents.profile import AgentProfile
    from agents.execution.tool_executor import ToolExecutor
    from llm.client import LLMClient


@dataclass(slots=True)
class ExecutionStep:
    step_type: str
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionContext:
    resolved_profile: Optional["AgentProfile"] = None
    system_prompt: str = ""
    tool_policy_text: str = ""
    tool_executor: Optional["ToolExecutor"] = None


class AgentStrategy(ABC):
    name: str = "simple"

    @abstractmethod
    async def execute(
        self,
        message: "AgentMessage",
        *,
        agent_id: str,
        llm_client: Optional["LLMClient"],
        tool_executor: "ToolExecutor",
        memory: Optional[Any],
        execution_context: Optional[ExecutionContext] = None,
    ) -> AsyncGenerator[ExecutionStep, None]:
        """Run the strategy and stream standardized execution steps."""
