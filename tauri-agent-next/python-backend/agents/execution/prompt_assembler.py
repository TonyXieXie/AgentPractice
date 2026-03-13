from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.execution.message_utils import get_system_prompt, render_current_message
from agents.message import AgentMessage


DEFAULT_TOOL_POLICY_TEXT = (
    "工具定义通过 API tools 字段提供；如需使用工具，请输出工具调用参数并等待工具结果。"
)


class PromptAssembler:
    def build_system_messages(
        self,
        message: AgentMessage,
        *,
        default_system_prompt: str,
        tool_policy_text: str = DEFAULT_TOOL_POLICY_TEXT,
        llm_client: Any = None,
    ) -> List[Dict[str, Any]]:
        parts: List[str] = []
        base = (default_system_prompt or "").strip()
        if base:
            parts.append(base)
        override = (get_system_prompt(message) or "").strip()
        if override:
            parts.append(override)
        policy = (tool_policy_text or "").strip()
        if policy:
            parts.append(policy)
        system_text = "\n\n".join(part for part in parts if part).strip()
        if not system_text:
            return []
        return [{"role": self._system_role_for_client(llm_client), "content": system_text}]

    def build_current_input(self, message: AgentMessage) -> List[Dict[str, Any]]:
        role = "user" if self._is_external_user_message(message) else "assistant"
        content = render_current_message(message)
        if not content.strip():
            return []
        return [{"role": role, "content": content}]

    def assemble(
        self,
        *,
        system_messages: List[Dict[str, Any]],
        history_messages: List[Dict[str, Any]],
        current_input: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        return [*system_messages, *history_messages, *current_input]

    def _system_role_for_client(self, llm_client: Any) -> str:
        api_format = str(
            getattr(getattr(llm_client, "config", None), "api_format", "") or ""
        ).strip()
        return "developer" if api_format == "openai_responses" else "system"

    def _is_external_user_message(self, message: AgentMessage) -> bool:
        sender_id = str(message.sender_id or "").strip()
        return sender_id == "external:http"
