"""
SimpleAgent - Backward Compatible Chat Agent

This agent maintains simple conversational behavior:
- No tool use
- Direct LLM conversation
- Simple message history
"""

from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime
import traceback
from .base import AgentStrategy, AgentStep
from message_processor import message_processor
from context_estimate import build_context_estimate


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
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.max_history = max_history
    
    def _merge_debug_context(
        self,
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]],
        agent_type: str,
        iteration: int
    ) -> Optional[Dict[str, Any]]:
        debug_ctx: Dict[str, Any] = {}
        if request_overrides and isinstance(request_overrides.get("_debug"), dict):
            debug_ctx.update(request_overrides.get("_debug", {}))
        if session_id:
            debug_ctx["session_id"] = session_id
        if "message_id" not in debug_ctx:
            debug_ctx["message_id"] = None
        debug_ctx["agent_type"] = agent_type
        debug_ctx["iteration"] = iteration
        return debug_ctx if debug_ctx else None

    async def execute(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List["Tool"],
        llm_client: "LLMClient",
        session_id: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute simple conversation.

        Args:
            user_input: User's message
            history: Conversation history
            tools: Available tools (ignored for simple agent)
            llm_client: LLM client
            session_id: Optional session ID
            request_overrides: Optional per-request overrides

        Yields:
            AgentStep with type "answer" containing LLM response
        """
        profile = getattr(llm_client.config, "api_profile", None) or getattr(llm_client.config, "api_type", None)
        profile = (profile or "openai").lower()
        system_role = "developer" if profile == "openai" else "system"
        user_content = None
        if request_overrides and request_overrides.get("user_content") is not None:
            user_content = request_overrides.get("user_content")
        messages = message_processor.build_messages_for_llm(
            user_message=user_content if user_content is not None else user_input,
            history=history,
            system_prompt=self.system_prompt,
            max_history=self.max_history,
            system_role=system_role
        )

        try:
            llm_overrides = dict(request_overrides) if request_overrides else {}
            debug_ctx = self._merge_debug_context(session_id, request_overrides, "simple", 0)
            if debug_ctx:
                llm_overrides["_debug"] = debug_ctx

            max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
            estimate = build_context_estimate(
                messages,
                tools_payload=None,
                max_tokens=max_tokens,
                updated_at=datetime.now().isoformat()
            )
            yield AgentStep(step_type="context_estimate", content="", metadata=estimate)

            response = await llm_client.chat(messages, llm_overrides if llm_overrides else None)
            content = response.get("content", "")

            processed_content = message_processor.postprocess_llm_response(content)

            llm_call_id = response.get("llm_call_id")
            if llm_call_id:
                from database import db
                db.update_llm_call_processed(llm_call_id, {"content": processed_content})

            yield AgentStep(
                step_type="answer",
                content=processed_content,
                metadata={
                    "agent_type": "simple",
                    "raw_response": response.get("raw_response", {})
                }
            )
        except Exception as e:
            yield AgentStep(
                step_type="error",
                content=f"LLM call failed: {str(e)}",
                metadata={"error": str(e), "traceback": traceback.format_exc()}
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
