from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx

from agents.execution.prompt_ir import PromptIR
from app_config import get_app_config
from llm.request_body_builder import RequestBodyBuilder
from models import LLMConfig


class LLMTransientError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.cause = cause


class LLMClient:
    """Standalone LLM client copied into the new backend boundary."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.request_body_builder = RequestBodyBuilder()
        self.timeout = self._resolve_timeout()
        self.max_retries, self.retry_base_delay, self.retry_max_delay = (
            self._resolve_retry_policy()
        )

    def _resolve_timeout(self) -> float:
        llm_cfg = get_app_config().get("llm", {})
        try:
            timeout = float(llm_cfg.get("timeout_sec", 180.0))
        except (TypeError, ValueError):
            timeout = 180.0
        return max(1.0, timeout)

    def _resolve_retry_policy(self) -> Tuple[int, float, float]:
        retry_cfg = get_app_config().get("llm", {}).get("retry", {})
        try:
            max_retries = int(retry_cfg.get("max_retries", 5))
        except (TypeError, ValueError):
            max_retries = 5
        try:
            base_delay = float(retry_cfg.get("base_delay_sec", 1.0))
        except (TypeError, ValueError):
            base_delay = 1.0
        try:
            max_delay = float(retry_cfg.get("max_delay_sec", 8.0))
        except (TypeError, ValueError):
            max_delay = 8.0
        return max(0, max_retries), max(0.0, base_delay), max(base_delay, max_delay)

    def _get_format(self) -> str:
        value = str(getattr(self.config, "api_format", "") or "").lower()
        if value in {"openai_responses", "responses", "response"}:
            return "openai_responses"
        return "openai_chat_completions"

    def _get_profile(self) -> str:
        return str(getattr(self.config, "api_profile", None) or "openai").lower()

    def _get_base_url(self) -> str:
        if self.config.base_url:
            return self.config.base_url.rstrip("/")
        defaults = {
            "openai": "https://api.openai.com/v1",
            "openai_compatible": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        }
        return defaults.get(self._get_profile(), "https://api.openai.com/v1")

    def _should_retry_status(self, status_code: Optional[int]) -> bool:
        if status_code is None:
            return False
        return status_code == 429 or 500 <= status_code <= 599

    def _is_rate_limited(self, status_code: Optional[int]) -> bool:
        return status_code == 429

    def _get_retry_delay(
        self,
        attempt: int,
        status_code: Optional[int] = None,
        is_network: bool = False,
    ) -> float:
        if is_network or self._is_rate_limited(status_code):
            return max(0.0, 10.0 * (attempt + 1))
        delay = self.retry_base_delay * (2**max(0, attempt))
        return min(self.retry_max_delay, delay) if self.retry_max_delay > 0 else delay

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def _raise_for_http_error(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if self._is_rate_limited(response.status_code):
                raise LLMTransientError(
                    f"Rate limited (HTTP {response.status_code}).",
                    status_code=response.status_code,
                    cause=exc,
                ) from exc
            raise

    async def chat(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._get_format() == "openai_responses":
            return await self._chat_openai_responses(prompt_ir, request_overrides)
        return await self._chat_openai(prompt_ir, request_overrides)

    async def chat_stream(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self.chat_stream_events(prompt_ir, request_overrides):
            if event.get("type") == "content":
                delta = event.get("delta", "")
                if delta:
                    yield delta

    async def chat_stream_events(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if self._get_format() == "openai_responses":
            async for event in self._chat_openai_responses_stream_events(
                prompt_ir, request_overrides
            ):
                yield event
            return
        async for event in self._chat_openai_stream_events(prompt_ir, request_overrides):
            yield event

    async def _chat_openai(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path, request_payload = self.request_body_builder.build(
            config=self.config,
            prompt_ir=prompt_ir,
            request_overrides=request_overrides,
            stream=False,
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self._get_base_url()}{path}",
                        headers=self._auth_headers(),
                        json=request_payload,
                    )
                except httpx.RequestError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_delay(attempt, is_network=True))
                        continue
                    raise LLMTransientError(f"Network error: {exc}", cause=exc) from exc
                if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                    await response.aread()
                    await asyncio.sleep(
                        self._get_retry_delay(attempt, status_code=response.status_code)
                    )
                    continue
                await self._raise_for_http_error(response)
                data = response.json()
                return {
                    "content": self._extract_chat_response_text(data),
                    "raw_response": data,
                    "reasoning_tokens": data.get("usage", {}).get("reasoning_tokens", 0),
                }
        raise RuntimeError("Unreachable retry loop in _chat_openai")

    async def _chat_openai_stream_events(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        path, request_payload = self.request_body_builder.build(
            config=self.config,
            prompt_ir=prompt_ir,
            request_overrides=request_overrides,
            stream=True,
        )

        full_text = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    async with client.stream(
                        "POST",
                        f"{self._get_base_url()}{path}",
                        headers=self._auth_headers(),
                        json=request_payload,
                    ) as response:
                        if (
                            self._should_retry_status(response.status_code)
                            and attempt < self.max_retries
                        ):
                            await response.aread()
                            await asyncio.sleep(
                                self._get_retry_delay(
                                    attempt, status_code=response.status_code
                                )
                            )
                            continue
                        await self._raise_for_http_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                yield {"type": "done", "content": full_text}
                                return
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            delta = self._extract_chat_stream_delta(event)
                            if delta:
                                full_text += delta
                                yield {"type": "content", "delta": delta}

                            reasoning = self._extract_chat_reasoning_delta(event)
                            if reasoning:
                                yield {"type": "reasoning", "delta": reasoning}

                            tool_delta = self._extract_chat_tool_call_delta(event)
                            if tool_delta:
                                yield tool_delta
                        yield {"type": "done", "content": full_text}
                        return
                except httpx.RequestError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_delay(attempt, is_network=True))
                        continue
                    raise LLMTransientError(f"Network error: {exc}", cause=exc) from exc
        raise RuntimeError("Unreachable retry loop in _chat_openai_stream_events")

    async def _chat_openai_responses(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path, request_payload = self.request_body_builder.build(
            config=self.config,
            prompt_ir=prompt_ir,
            request_overrides=request_overrides,
            stream=False,
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self._get_base_url()}{path}",
                        headers=self._auth_headers(),
                        json=request_payload,
                    )
                except httpx.RequestError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_delay(attempt, is_network=True))
                        continue
                    raise LLMTransientError(f"Network error: {exc}", cause=exc) from exc
                if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                    await response.aread()
                    await asyncio.sleep(
                        self._get_retry_delay(attempt, status_code=response.status_code)
                    )
                    continue
                await self._raise_for_http_error(response)
                data = response.json()
                return {
                    "content": self._extract_openai_responses_text(data),
                    "raw_response": data,
                }
        raise RuntimeError("Unreachable retry loop in _chat_openai_responses")

    async def _chat_openai_responses_stream_events(
        self,
        prompt_ir: PromptIR,
        request_overrides: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        path, request_payload = self.request_body_builder.build(
            config=self.config,
            prompt_ir=prompt_ir,
            request_overrides=request_overrides,
            stream=True,
        )

        full_text = ""
        tool_calls: Dict[int, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    async with client.stream(
                        "POST",
                        f"{self._get_base_url()}{path}",
                        headers=self._auth_headers(),
                        json=request_payload,
                    ) as response:
                        if (
                            self._should_retry_status(response.status_code)
                            and attempt < self.max_retries
                        ):
                            await response.aread()
                            await asyncio.sleep(
                                self._get_retry_delay(
                                    attempt, status_code=response.status_code
                                )
                            )
                            continue
                        await self._raise_for_http_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                yield {
                                    "type": "done",
                                    "content": full_text,
                                    "tool_calls": [
                                        tool_calls[index]
                                        for index in sorted(tool_calls.keys())
                                    ],
                                }
                                return
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            event_type = str(event.get("type", "") or "")
                            if event_type == "response.output_text.delta":
                                delta = str(event.get("delta", "") or "")
                                if delta:
                                    full_text += delta
                                    yield {"type": "content", "delta": delta}
                                continue

                            if event_type in {
                                "response.reasoning_summary_text.delta",
                                "response.reasoning_text.delta",
                            }:
                                delta = str(event.get("delta", "") or "")
                                if delta:
                                    yield {"type": "reasoning", "delta": delta}
                                continue

                            if event_type in {
                                "response.function_call_arguments.delta",
                                "response.function_call_arguments.done",
                            }:
                                output_index = int(event.get("output_index", 0) or 0)
                                call = tool_calls.setdefault(
                                    output_index,
                                    {
                                        "index": output_index,
                                        "id": event.get("call_id"),
                                        "name": event.get("name", ""),
                                        "arguments": "",
                                    },
                                )
                                if event.get("call_id"):
                                    call["id"] = event.get("call_id")
                                if event.get("name"):
                                    call["name"] = event.get("name")
                                if event.get("arguments") is not None:
                                    call["arguments"] = event.get("arguments")
                                delta = str(event.get("delta", "") or "")
                                if delta:
                                    call["arguments"] = f"{call['arguments']}{delta}"
                                yield {
                                    "type": "tool_call_delta",
                                    "index": output_index,
                                    "id": call.get("id"),
                                    "name": call.get("name", ""),
                                    "arguments_delta": delta,
                                    "arguments": call.get("arguments", ""),
                                }
                        yield {
                            "type": "done",
                            "content": full_text,
                            "tool_calls": [
                                tool_calls[index] for index in sorted(tool_calls.keys())
                            ],
                        }
                        return
                except httpx.RequestError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_delay(attempt, is_network=True))
                        continue
                    raise LLMTransientError(f"Network error: {exc}", cause=exc) from exc
        raise RuntimeError("Unreachable retry loop in _chat_openai_responses_stream_events")

    def _extract_openai_responses_text(self, data: Dict[str, Any]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text
        parts: List[str] = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(str(content["text"]))
        return "".join(parts)

    def _extract_chat_response_text(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if self._get_profile() == "deepseek" and message.get("reasoning_content"):
            reasoning = str(message.get("reasoning_content"))
            return f"[Reasoning]\n{reasoning}\n\n[Answer]\n{content}"
        return self._coerce_text(content)

    def _extract_chat_stream_delta(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return self._coerce_text(delta.get("content", ""))

    def _extract_chat_reasoning_delta(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        for key in (
            "reasoning",
            "reasoning_content",
            "thinking",
            "thinking_content",
            "analysis",
        ):
            if delta.get(key) is not None:
                return self._coerce_text(delta.get(key))
        return ""

    def _extract_chat_tool_call_delta(
        self, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        choices = data.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        tool_calls = delta.get("tool_calls") or []
        if not tool_calls:
            return None
        tool_call = tool_calls[0] or {}
        function = tool_call.get("function") or {}
        return {
            "type": "tool_call_delta",
            "index": tool_call.get("index", 0),
            "id": tool_call.get("id"),
            "name": function.get("name", ""),
            "arguments_delta": function.get("arguments", ""),
            "arguments": function.get("arguments", ""),
        }

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(self._coerce_text(item) for item in value)
        if isinstance(value, dict):
            for key in ("text", "content", "delta", "value"):
                if key in value and value[key] is not None:
                    return self._coerce_text(value[key])
        return str(value)


def create_llm_client(config: LLMConfig) -> LLMClient:
    return LLMClient(config)
