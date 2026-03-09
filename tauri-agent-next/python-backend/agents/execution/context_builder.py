from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.execution.strategy import ExecutionRequest
from agents.message import AgentMessage
from llm.client import create_llm_client
from models import LLMConfig


class ContextBuilder:
    def build_request(
        self,
        message: AgentMessage,
        *,
        agent_id: str,
        default_strategy: str = "simple",
    ) -> ExecutionRequest:
        payload = message.payload or {}
        request_overrides = self._as_dict(payload.get("request_overrides"))
        metadata = self._as_dict(message.metadata)
        metadata.update(self._as_dict(payload.get("metadata")))
        strategy_name = str(
            payload.get("strategy")
            or request_overrides.get("strategy")
            or default_strategy
            or "simple"
        ).lower()
        tool_arguments = self._as_dict(payload.get("tool_arguments"))
        if not tool_arguments and isinstance(request_overrides.get("tool_arguments"), dict):
            tool_arguments = self._as_dict(request_overrides.get("tool_arguments"))

        return ExecutionRequest(
            agent_id=agent_id,
            run_id=message.run_id,
            message_id=message.id,
            correlation_id=message.correlation_id,
            user_input=str(payload.get("content", "") or ""),
            history=self._normalize_history(payload.get("history")),
            request_overrides=request_overrides,
            metadata=metadata,
            llm_config=self._optional_dict(payload.get("llm_config"))
            or self._optional_dict(request_overrides.get("llm_config")),
            system_prompt=self._optional_str(
                payload.get("system_prompt") or request_overrides.get("system_prompt")
            ),
            work_path=self._optional_str(
                payload.get("work_path") or request_overrides.get("work_path")
            ),
            strategy_name=strategy_name,
            tool_name=self._optional_str(
                payload.get("tool_name") or request_overrides.get("tool_name")
            ),
            tool_arguments=tool_arguments,
        )

    def build_llm_client(self, request: ExecutionRequest):
        if not request.llm_config:
            return None
        config = LLMConfig.model_validate(request.llm_config)
        return create_llm_client(config)

    def build_messages(
        self,
        request: ExecutionRequest,
        *,
        llm_client=None,
        default_system_prompt: str = "You are a helpful AI assistant.",
        max_history: int = 10,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        system_prompt = request.system_prompt or default_system_prompt
        profile = str(
            getattr(getattr(llm_client, "config", None), "api_profile", None) or "openai"
        ).lower()
        system_role = "developer" if profile == "openai" else "system"
        if system_prompt:
            messages.append({"role": system_role, "content": system_prompt})

        history = request.history[-max_history:] if max_history > 0 else list(request.history)
        for item in history:
            role = self._optional_str(item.get("role"))
            content = item.get("content")
            if not role or content is None:
                continue
            messages.append({"role": role, "content": deepcopy(content)})

        messages.append({"role": "user", "content": request.user_input})
        return messages

    def build_llm_request_overrides(self, request: ExecutionRequest) -> Dict[str, Any]:
        filtered: Dict[str, Any] = {}
        for key, value in request.request_overrides.items():
            if key in {
                "strategy",
                "llm_config",
                "tool_name",
                "tool_arguments",
                "work_path",
                "system_prompt",
                "stream",
            }:
                continue
            filtered[key] = deepcopy(value)
        return filtered

    def _normalize_history(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        history: List[Dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = self._optional_str(item.get("role"))
            content = item.get("content")
            if not role or content is None:
                continue
            history.append({"role": role, "content": deepcopy(content)})
        return history

    def _as_dict(self, value: Any) -> Dict[str, Any]:
        return deepcopy(value) if isinstance(value, dict) else {}

    def _optional_dict(self, value: Any) -> Optional[Dict[str, Any]]:
        return deepcopy(value) if isinstance(value, dict) else None

    def _optional_str(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
