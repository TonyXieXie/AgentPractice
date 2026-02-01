"""
Agent Executor - Orchestrates agent execution

Provides:
- Agent factory (creates agents based on type)
- Execution orchestration
- Tool management
- Error handling
"""

from typing import List, Dict, Optional, AsyncGenerator, Any
from .base import AgentStep, AgentStrategy
from .simple import SimpleAgent
from .react import ReActAgent
from tools.base import Tool, ToolRegistry


class AgentExecutor:
    """
    Orchestrates agent execution.

    Responsibilities:
    - Manage agent strategy
    - Provide tools to agent
    - Stream execution steps
    - Handle errors
    """

    def __init__(
        self,
        strategy: AgentStrategy,
        tools: List[Tool],
        llm_client: "LLMClient"
    ):
        """
        Initialize executor.

        Args:
            strategy: Agent strategy to execute
            tools: Available tools
            llm_client: LLM client for API calls
        """
        self.strategy = strategy
        self.tools = tools
        self.llm_client = llm_client

    async def run(
        self,
        user_input: str,
        history: List[Dict[str, str]] = None,
        session_id: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute agent and stream steps.

        Args:
            user_input: User's message
            history: Conversation history
            session_id: Optional session ID
            request_overrides: Optional per-request overrides (e.g. response_format)

        Yields:
            AgentStep for each step of execution
        """
        history = history or []

        try:
            async for step in self.strategy.execute(
                user_input=user_input,
                history=history,
                tools=self.tools,
                llm_client=self.llm_client,
                session_id=session_id,
                request_overrides=request_overrides
            ):
                yield step

        except Exception as e:
            # Catch any unhandled errors
            yield AgentStep(
                step_type="error",
                content=f"Agent execution failed: {str(e)}",
                metadata={"error": str(e), "error_type": type(e).__name__}
            )


def create_agent_executor(
    agent_type: str,
    llm_client: "LLMClient",
    tools: Optional[List[Tool]] = None,
    **kwargs
) -> AgentExecutor:
    """
    Factory function to create agent executor.

    Args:
        agent_type: Type of agent ("simple", "react", etc.)
        llm_client: LLM client
        tools: Optional list of tools (defaults to all registered tools)
        **kwargs: Additional arguments for specific agent types

    Returns:
        AgentExecutor instance

    Raises:
        ValueError: If agent_type is unknown
    """
    if tools is None:
        tools = ToolRegistry.get_all()

    if agent_type == "simple":
        strategy = SimpleAgent(
            system_prompt=kwargs.get("system_prompt"),
            max_history=kwargs.get("max_history", 10)
        )
    elif agent_type == "react":
        strategy = ReActAgent(
            max_iterations=kwargs.get("max_iterations", 5)
        )
    else:
        raise ValueError(
            f"Unknown agent type: '{agent_type}'. "
            f"Supported types: simple, react"
        )

    return AgentExecutor(strategy, tools, llm_client)
