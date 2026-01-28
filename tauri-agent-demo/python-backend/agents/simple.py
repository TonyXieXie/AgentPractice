"""
SimpleAgent - Backward Compatible Chat Agent

This agent maintains the existing simple conversational behavior:
- No tool use
- Direct LLM conversation
- Simple message history
"""

from typing import List, Dict, Any, AsyncGenerator, Optional
from .base import AgentStrategy, AgentStep
from message_processor import message_processor


class SimpleAgent(AgentStrategy):
    """
    Simple conversational agent (backward compatible).
    
    This agent provides the same behavior as the original chat system:
    - Builds messages from history + user input
    - Single LLM API call
    - Returns complete response
    - No tool usage
    """
    
    def __init__(self, system_prompt: Optional[str] = None, max_history: int = 10):
        """
        Initialize SimpleAgent.
        
        Args:
            system_prompt: Optional system prompt
            max_history: Maximum number of history messages to include
        """
        self.system_prompt = system_prompt or "你是一个有帮助的AI助手。"
        self.max_history = max_history
    
    async def execute(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List["Tool"],
        llm_client: "LLMClient",
        session_id: Optional[str] = None
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute simple conversation.
        
        Args:
            user_input: User's message
            history: Conversation history
            tools: Available tools (ignored for simple agent)
            llm_client: LLM client
            session_id: Optional session ID
        
        Yields:
            AgentStep with type "answer" containing LLM response
        """
        # Build messages using existing logic
        messages = message_processor.build_messages_for_llm(
            user_message=user_input,
            history=history,
            system_prompt=self.system_prompt,
            max_history=self.max_history
        )
        
        # Call LLM
        try:
            response = await llm_client.chat(messages)
            content = response.get("content", "")
            
            # Post-process response
            processed_content = message_processor.postprocess_llm_response(content)
            
            # Yield final answer
            yield AgentStep(
                step_type="answer",
                content=processed_content,
                metadata={
                    "agent_type": "simple",
                    "raw_response": response.get("raw_response", {})
                }
            )
        except Exception as e:
            # Yield error step
            yield AgentStep(
                step_type="error",
                content=f"LLM调用失败: {str(e)}",
                metadata={"error": str(e)}
            )
    
    def build_prompt(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List["Tool"],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build prompt (not used for SimpleAgent as it uses message_processor).
        
        Returns:
            System prompt
        """
        return self.system_prompt
    
    def get_max_iterations(self) -> int:
        """SimpleAgent only needs 1 iteration"""
        return 1
