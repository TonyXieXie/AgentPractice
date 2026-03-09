from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional

if TYPE_CHECKING:
    from agents.execution.context_builder import ContextBuilder
    from agents.execution.tool_executor import ToolExecutor
    from llm.client import LLMClient


@dataclass(slots=True)
class ExecutionRequest:
    agent_id: str
    run_id: Optional[str]
    message_id: Optional[str]
    correlation_id: Optional[str]
    user_input: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    request_overrides: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    llm_config: Optional[Dict[str, Any]] = None
    system_prompt: Optional[str] = None
    work_path: Optional[str] = None
    strategy_name: str = "simple"
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionStep:
    step_type: str
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentStrategy(ABC):
    name: str = "simple"

    @abstractmethod
    async def execute(
        self,
        request: ExecutionRequest,
        *,
        llm_client: Optional["LLMClient"],
        tool_executor: "ToolExecutor",
        context_builder: "ContextBuilder",
    ) -> AsyncGenerator[ExecutionStep, None]:
        """Run the strategy and stream standardized execution steps."""
