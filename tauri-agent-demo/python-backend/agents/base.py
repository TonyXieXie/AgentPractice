"""
Agent Framework Base Classes

This module defines the core abstractions for the agent system:
- AgentStep: Represents a single step in agent execution
- AgentStrategy: Abstract base class for all agent types
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator, Optional
from dataclasses import dataclass, field

@dataclass
class AgentStep:
    """
    Represents a single step in agent execution.
    
    Used for streaming agent thoughts, actions, observations, and final answers.
    """
    step_type: str  # "thought", "action", "observation", "answer", "error"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "step_type": self.step_type,
            "content": self.content,
            "metadata": self.metadata
        }


class AgentStrategy(ABC):
    """
    Abstract base class for all agent strategies.
    
    Subclasses implement specific agent patterns:
    - SimpleAgent: Direct LLM conversation (existing behavior)
    - ReActAgent: Reasoning + Acting loop with tool use
    - PlanExecuteAgent: Plan first, then execute steps
    - TreeOfThoughtsAgent: Explore multiple reasoning paths
    """
    
    @abstractmethod
    async def execute(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List["Tool"],
        llm_client: "LLMClient",
        session_id: Optional[str] = None
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute the agent strategy.
        
        Args:
            user_input: Current user message
            history: Conversation history (list of {role, content} dicts)
            tools: Available tools for the agent
            llm_client: LLM client for making API calls
            session_id: Optional session ID for context
        
        Yields:
            AgentStep: Each step of agent execution (thought, action, observation, etc.)
        """
        pass
    
    @abstractmethod
    def build_prompt(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List["Tool"],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build the prompt for this agent strategy.
        
        Args:
            user_input: Current user message
            history: Conversation history
            tools: Available tools
            additional_context: Additional context (scratchpad, etc.)
        
        Returns:
            Formatted prompt string
        """
        pass
    
    def get_max_iterations(self) -> int:
        """
        Get maximum iterations for this agent type.
        
        Returns:
            Max iterations (default 1 for simple agents)
        """
        return 1
