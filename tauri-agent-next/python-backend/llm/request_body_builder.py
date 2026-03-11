from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agents.execution.prompt_ir import PromptIR
from models import LLMConfig


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_coerce_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "delta", "value"):
            if key in value and value[key] is not None:
                return _coerce_text(value[key])
    return str(value)


def _normalize_api_format(value: Any) -> str:
    lowered = str(value or "").lower()
    if lowered in {"openai_responses", "responses", "response"}:
        return "openai_responses"
    return "openai_chat_completions"


class RequestBodyBuilder:
    def build(
        self,
        *,
        config: LLMConfig,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
        stream: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        messages = prompt_ir.messages if isinstance(prompt_ir, PromptIR) else []
        api_format = _normalize_api_format(getattr(config, "api_format", None))
        if api_format == "openai_responses":
            return self._build_openai_responses(
                config=config,
                messages=messages,
                request_overrides=request_overrides,
                stream=stream,
            )
        return self._build_openai_chat_completions(
            config=config,
            messages=messages,
            request_overrides=request_overrides,
            stream=stream,
        )

    def _apply_reasoning_params(self, config: LLMConfig, request_payload: Dict[str, Any]) -> None:
        profile = str(getattr(config, "api_profile", None) or "openai").lower()
        model_lower = str(getattr(config, "model", "") or "").lower()
        if profile == "openai" and ("o1" in model_lower or "gpt-5" in model_lower):
            request_payload.pop("temperature", None)
            request_payload["reasoning"] = {
                "effort": getattr(config, "reasoning_effort", "medium") or "medium",
                "summary": getattr(config, "reasoning_summary", "detailed") or "detailed",
            }
        if profile == "deepseek" and "reasoner" in model_lower:
            request_payload.pop("temperature", None)

    def _apply_overrides(
        self,
        request_payload: Dict[str, Any],
        request_overrides: Optional[Dict[str, Any]],
    ) -> None:
        if not request_overrides:
            return
        request_payload.update(
            {
                key: value
                for key, value in request_overrides.items()
                if not str(key).startswith("_") and value is not None
            }
        )

    def _build_openai_chat_completions(
        self,
        *,
        config: LLMConfig,
        messages: List[Dict[str, Any]],
        request_overrides: Optional[Dict[str, Any]],
        stream: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        request_payload: Dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if stream:
            request_payload["stream"] = True
        self._apply_reasoning_params(config, request_payload)
        self._apply_overrides(request_payload, request_overrides)
        return "/chat/completions", request_payload

    def _build_openai_responses(
        self,
        *,
        config: LLMConfig,
        messages: List[Dict[str, Any]],
        request_overrides: Optional[Dict[str, Any]],
        stream: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        request_payload: Dict[str, Any] = {
            "model": config.model,
            "input": self._build_responses_input(messages),
            "temperature": config.temperature,
            "max_output_tokens": config.max_tokens,
        }
        if stream:
            request_payload["stream"] = True
        self._apply_reasoning_params(config, request_payload)
        self._apply_overrides(request_payload, request_overrides)
        return "/responses", request_payload

    def _build_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            item_type = str(message.get("type", "") or "")
            if item_type in {"function_call", "function_call_output", "reasoning"}:
                items.append({key: value for key, value in message.items() if value is not None})
                continue

            role = str(message.get("role", "user") or "user")
            content = message.get("content", "")
            text = content if isinstance(content, str) else _coerce_text(content)
            text_type = "output_text" if role == "assistant" else "input_text"
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": text_type, "text": text}],
                }
            )
        return items
