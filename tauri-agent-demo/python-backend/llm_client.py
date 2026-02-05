from typing import Optional, List, Dict, Any, Tuple
import httpx
from models import LLMConfig
from app_config import get_app_config


class LLMClient:
    """Unified LLM client supporting multiple formats and profiles."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.timeout = self._resolve_timeout()
        self.max_retries, self.retry_base_delay, self.retry_max_delay = self._resolve_retry_policy()

    def _resolve_timeout(self) -> float:
        app_config = get_app_config()
        timeout = app_config.get("llm", {}).get("timeout_sec", 180.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            return 180.0
        return max(1.0, timeout)

    def _resolve_retry_policy(self) -> Tuple[int, float, float]:
        app_config = get_app_config()
        retry_cfg = app_config.get("llm", {}).get("retry", {})
        max_retries = retry_cfg.get("max_retries", 5)
        base_delay = retry_cfg.get("base_delay_sec", 1.0)
        max_delay = retry_cfg.get("max_delay_sec", 8.0)
        try:
            max_retries = int(max_retries)
        except (TypeError, ValueError):
            max_retries = 5
        try:
            base_delay = float(base_delay)
        except (TypeError, ValueError):
            base_delay = 1.0
        try:
            max_delay = float(max_delay)
        except (TypeError, ValueError):
            max_delay = 8.0
        if max_retries < 0:
            max_retries = 0
        if base_delay < 0:
            base_delay = 0.0
        if max_delay < base_delay:
            max_delay = base_delay
        return max_retries, base_delay, max_delay

    def _should_retry_status(self, status_code: Optional[int]) -> bool:
        if status_code is None:
            return False
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return False
        return 500 <= code <= 599

    def _get_retry_delay(self, attempt: int) -> float:
        if attempt < 0:
            attempt = 0
        delay = self.retry_base_delay * (2 ** attempt)
        if self.retry_max_delay > 0:
            delay = min(self.retry_max_delay, delay)
        return max(0.0, delay)

    async def chat(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send a non-streaming chat request.

        Args:
            messages: List of {"role": "user|assistant|system|developer", "content": "..."}
            request_overrides: Optional per-request options

        Returns:
            {"content": str, "raw_response": dict}
        """
        fmt = self._get_format()
        profile = self._get_profile()

        if fmt == "openai_responses":
            if profile not in ("openai", "openai_compatible"):
                raise ValueError(f"Responses format not supported for profile '{profile}'")
            return await self._chat_openai_responses(messages, request_overrides)

        return await self._chat_openai(messages, request_overrides)

    async def chat_stream(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """Send a streaming chat request and yield text chunks."""
        fmt = self._get_format()
        profile = self._get_profile()

        if fmt == "openai_responses":
            if profile not in ("openai", "openai_compatible"):
                raise ValueError(f"Responses format not supported for profile '{profile}'")
            async for chunk in self._chat_openai_responses_stream(messages, request_overrides):
                yield chunk
            return

        async for chunk in self._chat_openai_stream(messages, request_overrides):
            yield chunk

    async def chat_stream_events(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """Send a streaming chat request and yield structured events."""
        fmt = self._get_format()
        profile = self._get_profile()

        if fmt == "openai_responses":
            if profile not in ("openai", "openai_compatible"):
                raise ValueError(f"Responses format not supported for profile '{profile}'")
            async for event in self._chat_openai_responses_stream_events(messages, request_overrides):
                yield event
            return

        if profile not in ("openai", "openai_compatible", "deepseek", "zhipu"):
            raise ValueError(f"Streaming not supported for profile '{profile}'")

        async for event in self._chat_openai_stream_events(messages, request_overrides):
            yield event

    def _get_format(self) -> str:
        fmt = getattr(self.config, "api_format", None) or "openai_chat_completions"
        fmt = str(fmt).lower()
        if fmt in ("openai_responses", "responses", "response"):
            return "openai_responses"
        return "openai_chat_completions"

    def _get_profile(self) -> str:
        profile = getattr(self.config, "api_profile", None)
        if not profile:
            profile = getattr(self.config, "api_type", None)
        return str(profile or "openai").lower()

    def _get_base_url(self) -> str:
        if self.config.base_url:
            return self.config.base_url

        profile = self._get_profile()
        defaults = {
            "openai": "https://api.openai.com/v1",
            "openai_compatible": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4"
        }
        return defaults.get(profile, "https://api.openai.com/v1")

    def _build_openai_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        input_items: List[Dict[str, Any]] = []

        def add_text(items: List[Dict[str, Any]], role: str, text: Any):
            if text is None:
                return
            text_value = str(text)
            if not text_value:
                return
            item_type = "output_text" if role == "assistant" else "input_text"
            items.append({"type": item_type, "text": text_value})

        def add_image(items: List[Dict[str, Any]], role: str, image_url: Any):
            if role == "assistant":
                return
            url = ""
            if isinstance(image_url, dict):
                url = image_url.get("url") or image_url.get("source") or ""
            elif isinstance(image_url, str):
                url = image_url
            if not url:
                return
            items.append({"type": "input_image", "image_url": {"url": url}})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            content_items: List[Dict[str, Any]] = []

            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        part_type = str(part.get("type", "") or "").lower()
                        if part_type in ("text", "input_text", "output_text"):
                            add_text(content_items, role, part.get("text") or part.get("content"))
                        elif part_type in ("image_url", "input_image"):
                            add_image(content_items, role, part.get("image_url"))
                        elif "text" in part:
                            add_text(content_items, role, part.get("text"))
                    else:
                        add_text(content_items, role, part)
            else:
                add_text(content_items, role, content)

            if not content_items:
                add_text(content_items, role, "")

            input_items.append({
                "type": "message",
                "role": role,
                "content": content_items
            })

        return input_items

    def _extract_openai_responses_text(self, data: Dict[str, Any]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text
        output = data.get("output", [])
        parts: List[str] = []
        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and "text" in content:
                        parts.append(content["text"])
        return "".join(parts)

    def _extract_chat_response_text(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message", {}) or {}
        content = message.get("content", "")
        if self._get_profile() == "deepseek":
            reasoning_content = message.get("reasoning_content", "")
            if reasoning_content:
                return f"[Reasoning]\n{reasoning_content}\n\n[Answer]\n{content}"
        return content

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("text", "content", "delta", "value", "reasoning", "thinking"):
                if key in value:
                    coerced = self._coerce_text(value.get(key))
                    if coerced:
                        return coerced
            return str(value) if value else ""
        if isinstance(value, list):
            parts = [self._coerce_text(item) for item in value]
            return "".join([part for part in parts if part])
        return str(value)

    def _extract_reasoning_delta(self, delta: Dict[str, Any]) -> str:
        for key in ("reasoning", "reasoning_content", "thinking", "thinking_content", "analysis"):
            if key in delta and delta.get(key) is not None:
                return self._coerce_text(delta.get(key))
        delta_type = str(delta.get("type", "") or "").lower()
        if delta_type in ("thinking", "reasoning", "analysis"):
            for key in ("content", "text", "delta", "value"):
                if key in delta and delta.get(key) is not None:
                    return self._coerce_text(delta.get(key))
        return ""

    def _get_debug_context(self, request_overrides: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not request_overrides:
            return None
        debug_ctx = request_overrides.get("_debug")
        if isinstance(debug_ctx, dict):
            return debug_ctx
        return None

    def _get_stop_event(self, request_overrides: Optional[Dict[str, Any]]):
        if not request_overrides:
            return None
        return request_overrides.get("_stop_event")

    def _should_store_raw(self, debug_ctx: Optional[Dict[str, Any]]) -> bool:
        if not debug_ctx:
            return True
        return bool(debug_ctx.get("store_raw_response", True))

    def _save_llm_call(
        self,
        debug_ctx: Dict[str, Any],
        stream: bool,
        request_payload: Dict[str, Any],
        response_json: Dict[str, Any],
        response_text: str
    ) -> Optional[int]:
        try:
            from database import db
        except Exception:
            return None

        session_id = debug_ctx.get("session_id")
        if not session_id:
            return None

        message_id = debug_ctx.get("message_id")
        agent_type = debug_ctx.get("agent_type")
        iteration = debug_ctx.get("iteration")

        response_payload = response_json if self._should_store_raw(debug_ctx) else None
        llm_call_id = db.save_llm_call(
            session_id=session_id,
            message_id=message_id,
            agent_type=agent_type,
            iteration=iteration,
            stream=stream,
            api_profile=self._get_profile(),
            api_format=self._get_format(),
            model=self.config.model,
            request_json=request_payload,
            response_json=response_payload,
            response_text=response_text,
            processed_json=None
        )
        debug_ctx["llm_call_id"] = llm_call_id
        return llm_call_id

    def _apply_reasoning_params(self, request_payload: Dict[str, Any]) -> None:
        profile = self._get_profile()
        model_lower = self.config.model.lower()

        if profile == "openai" and ("o1" in model_lower or "gpt-5" in model_lower):
            request_payload.pop("temperature", None)
            reasoning_effort = getattr(self.config, "reasoning_effort", "medium")
            reasoning_summary = getattr(self.config, "reasoning_summary", "detailed")
            request_payload["reasoning"] = {
                "effort": reasoning_effort,
                "summary": reasoning_summary
            }
            print(f"[Reasoning Mode] effort={reasoning_effort}, summary={reasoning_summary}")

        if profile == "deepseek" and "reasoner" in model_lower:
            request_payload.pop("temperature", None)

    async def _log_http_error(self, exc: httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code if response is not None else "unknown"
        detail_text = ""
        detail_json = None
        if response is not None:
            try:
                raw = await response.aread()
                detail_text = raw.decode("utf-8", errors="replace")
            except Exception as read_error:
                try:
                    detail_text = response.text
                except Exception:
                    detail_text = f"<failed to read response body: {read_error}>"
        if detail_text:
            try:
                import json
                detail_json = json.loads(detail_text)
            except Exception:
                detail_json = None
        log_text = detail_text
        if len(log_text) > 4000:
            log_text = log_text[:4000] + "...<truncated>"
        print(f"[LLM HTTP Error] {exc} | status={status} | body={log_text}")
        return detail_text, detail_json, status

    async def _chat_openai(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """OpenAI-compatible Chat Completions API."""
        base_url = self._get_base_url()

        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }
        if request_overrides:
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)

        self._apply_reasoning_params(request_payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                )
                if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                    await response.aread()
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail_text, detail_json, status = await self._log_http_error(exc)
                    if debug_ctx:
                        error_payload = {"status": status, "message": str(exc)}
                        if detail_json is not None:
                            error_payload["body"] = detail_json
                        elif detail_text:
                            error_payload["body"] = detail_text
                        self._save_llm_call(
                            debug_ctx,
                            stream=False,
                            request_payload=request_payload,
                            response_json={"error": error_payload},
                            response_text=detail_text or str(exc)
                        )
                    raise
                data = response.json()

                usage = data.get("usage", {})
                reasoning_tokens = usage.get("reasoning_tokens", 0)
                if reasoning_tokens > 0:
                    print(f"[Reasoning Tokens] {reasoning_tokens} tokens used for reasoning")

                response_text = self._extract_chat_response_text(data)
                llm_call_id = None
                if debug_ctx:
                    llm_call_id = self._save_llm_call(
                        debug_ctx,
                        stream=False,
                        request_payload=request_payload,
                        response_json=data,
                        response_text=response_text
                    )

                result = {
                    "content": response_text,
                    "raw_response": data,
                    "reasoning_tokens": reasoning_tokens
                }
                if llm_call_id is not None:
                    result["llm_call_id"] = llm_call_id
                return result

    async def _chat_openai_stream(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """OpenAI-compatible Chat Completions streaming API."""
        import json

        base_url = self._get_base_url()
        stop_event = self._get_stop_event(request_overrides)
        stopped = False

        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }
        if request_overrides:
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)
        events: List[Dict[str, Any]] = []
        full_text = ""

        self._apply_reasoning_params(request_payload)

        completed = False
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                should_retry = False
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                ) as response:
                    if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                        await response.aread()
                        should_retry = True
                    else:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            detail_text, detail_json, status = await self._log_http_error(exc)
                            if debug_ctx:
                                error_payload = {"status": status, "message": str(exc)}
                                if detail_json is not None:
                                    error_payload["body"] = detail_json
                                elif detail_text:
                                    error_payload["body"] = detail_text
                                self._save_llm_call(
                                    debug_ctx,
                                    stream=True,
                                    request_payload=request_payload,
                                    response_json={"error": error_payload},
                                    response_text=detail_text or str(exc)
                                )
                            raise
                        async for line in response.aiter_lines():
                            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                stopped = True
                                break
                            if line.startswith("data: "):
                                data = line[6:]
                                if data == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data)
                                    events.append(chunk)
                                    choices = chunk.get("choices") or []
                                    if not choices:
                                        continue
                                    delta = choices[0].get("delta", {}) or {}
                                    delta_type = str(delta.get("type", "") or "").lower()
                                    if "content" in delta:
                                        text_delta = self._coerce_text(delta.get("content"))
                                        if text_delta and delta_type not in ("thinking", "reasoning", "analysis"):
                                            full_text += text_delta
                                            yield text_delta
                                except (json.JSONDecodeError, KeyError):
                                    continue
                        if stopped:
                            return
                if should_retry:
                    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                        return
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                completed = True
                break

        if completed and debug_ctx:
            self._save_llm_call(
                debug_ctx,
                stream=True,
                request_payload=request_payload,
                response_json={"events": events},
                response_text=full_text
            )

    async def _chat_openai_stream_events(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """OpenAI-compatible Chat Completions streaming API (structured events)."""
        import json

        base_url = self._get_base_url()
        stop_event = self._get_stop_event(request_overrides)
        stopped = False

        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }
        if request_overrides:
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)
        events: List[Dict[str, Any]] = []
        full_text = ""
        tool_calls: Dict[int, Dict[str, Any]] = {}

        self._apply_reasoning_params(request_payload)

        completed = False
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                should_retry = False
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                ) as response:
                    if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                        await response.aread()
                        should_retry = True
                    else:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            detail_text, detail_json, status = await self._log_http_error(exc)
                            if debug_ctx:
                                error_payload = {"status": status, "message": str(exc)}
                                if detail_json is not None:
                                    error_payload["body"] = detail_json
                                elif detail_text:
                                    error_payload["body"] = detail_text
                                self._save_llm_call(
                                    debug_ctx,
                                    stream=True,
                                    request_payload=request_payload,
                                    response_json={"error": error_payload},
                                    response_text=detail_text or str(exc)
                                )
                            raise
                        async for line in response.aiter_lines():
                            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                stopped = True
                                break
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            events.append(chunk)
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {}) or {}
                            delta_type = str(delta.get("type", "") or "").lower()

                            if "content" in delta:
                                text_delta = self._coerce_text(delta.get("content"))
                                if text_delta and delta_type not in ("thinking", "reasoning", "analysis"):
                                    full_text += text_delta
                                    yield {"type": "content", "delta": text_delta}

                            reasoning_delta = self._extract_reasoning_delta(delta)
                            if reasoning_delta:
                                yield {"type": "reasoning", "delta": reasoning_delta}

                            if "tool_calls" in delta:
                                for tool_delta in delta.get("tool_calls", []) or []:
                                    index = tool_delta.get("index", 0)
                                    call = tool_calls.get(index)
                                    if not call:
                                        call = {
                                            "index": index,
                                            "id": tool_delta.get("id"),
                                            "type": tool_delta.get("type", "function"),
                                            "function": {"name": "", "arguments": ""}
                                        }
                                        tool_calls[index] = call

                                    if tool_delta.get("id"):
                                        call["id"] = tool_delta.get("id")
                                    func = tool_delta.get("function", {}) or {}
                                    name_updated = False
                                    if "name" in func and func["name"]:
                                        call["function"]["name"] = func["name"]
                                        name_updated = True
                                    if "arguments" in func and func["arguments"] is not None:
                                        call["function"]["arguments"] += func["arguments"]
                                        yield {
                                            "type": "tool_call_delta",
                                            "index": index,
                                            "id": call.get("id"),
                                            "name": call["function"].get("name", ""),
                                            "arguments_delta": func.get("arguments", ""),
                                            "arguments": call["function"].get("arguments", "")
                                        }
                                    elif name_updated:
                                        yield {
                                            "type": "tool_call_delta",
                                            "index": index,
                                            "id": call.get("id"),
                                            "name": call["function"].get("name", ""),
                                            "arguments_delta": "",
                                            "arguments": call["function"].get("arguments", "")
                                        }
                        if stopped:
                            pass
                if should_retry:
                    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                        return
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                completed = True
                break

        tool_calls_list = [tool_calls[idx] for idx in sorted(tool_calls.keys())]

        if completed and debug_ctx:
            self._save_llm_call(
                debug_ctx,
                stream=True,
                request_payload=request_payload,
                response_json={"events": events},
                response_text=full_text
            )

        yield {"type": "done", "content": full_text, "tool_calls": tool_calls_list, "raw_events": events, "stopped": stopped}

    async def _chat_openai_responses(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """OpenAI Responses API."""
        base_url = self._get_base_url()

        request_payload = {
            "model": self.config.model,
            "input": self._build_openai_responses_input(messages),
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens
        }
        if request_overrides:
            if request_overrides.get("input") is not None:
                request_payload["input"] = request_overrides["input"]
            if request_overrides.get("previous_response_id") is not None:
                request_payload["previous_response_id"] = request_overrides["previous_response_id"]
            if request_overrides.get("instructions") is not None:
                request_payload["instructions"] = request_overrides["instructions"]
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)

        self._apply_reasoning_params(request_payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                response = await client.post(
                    f"{base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                )
                if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                    await response.aread()
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail_text, detail_json, status = await self._log_http_error(exc)
                    if debug_ctx:
                        error_payload = {"status": status, "message": str(exc)}
                        if detail_json is not None:
                            error_payload["body"] = detail_json
                        elif detail_text:
                            error_payload["body"] = detail_text
                        self._save_llm_call(
                            debug_ctx,
                            stream=False,
                            request_payload=request_payload,
                            response_json={"error": error_payload},
                            response_text=detail_text or str(exc)
                        )
                    raise
                data = response.json()
                response_text = self._extract_openai_responses_text(data)
                llm_call_id = None
                if debug_ctx:
                    llm_call_id = self._save_llm_call(
                        debug_ctx,
                        stream=False,
                        request_payload=request_payload,
                        response_json=data,
                        response_text=response_text
                    )

                result = {
                    "content": response_text,
                    "raw_response": data
                }
                if llm_call_id is not None:
                    result["llm_call_id"] = llm_call_id
                return result

    async def _chat_openai_responses_stream(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """OpenAI Responses streaming API."""
        import json

        base_url = self._get_base_url()
        stop_event = self._get_stop_event(request_overrides)
        stopped = False

        request_payload = {
            "model": self.config.model,
            "input": self._build_openai_responses_input(messages),
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens,
            "stream": True
        }
        if request_overrides:
            if request_overrides.get("input") is not None:
                request_payload["input"] = request_overrides["input"]
            if request_overrides.get("previous_response_id") is not None:
                request_payload["previous_response_id"] = request_overrides["previous_response_id"]
            if request_overrides.get("instructions") is not None:
                request_payload["instructions"] = request_overrides["instructions"]
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)
        events: List[Dict[str, Any]] = []
        full_text = ""

        self._apply_reasoning_params(request_payload)

        completed = False
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                should_retry = False
                async with client.stream(
                    "POST",
                    f"{base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                ) as response:
                    if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                        await response.aread()
                        should_retry = True
                    else:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            detail_text, detail_json, status = await self._log_http_error(exc)
                            if debug_ctx:
                                error_payload = {"status": status, "message": str(exc)}
                                if detail_json is not None:
                                    error_payload["body"] = detail_json
                                elif detail_text:
                                    error_payload["body"] = detail_text
                                self._save_llm_call(
                                    debug_ctx,
                                    stream=True,
                                    request_payload=request_payload,
                                    response_json={"error": error_payload},
                                    response_text=detail_text or str(exc)
                                )
                            raise
                        async for line in response.aiter_lines():
                            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                stopped = True
                                break
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                event = json.loads(data)
                                events.append(event)
                            except json.JSONDecodeError:
                                continue

                            event_type = event.get("type", "")
                            if event_type == "response.output_text.delta":
                                delta = event.get("delta", "")
                                if delta:
                                    full_text += delta
                                    yield delta
                            elif event_type in ("response.completed", "response.failed", "response.cancelled"):
                                break
                        if stopped:
                            return
                if should_retry:
                    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                        return
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                completed = True
                break

        if completed and debug_ctx:
            self._save_llm_call(
                debug_ctx,
                stream=True,
                request_payload=request_payload,
                response_json={"events": events},
                response_text=full_text
            )

    async def _chat_openai_responses_stream_events(self, messages: List[Dict[str, Any]], request_overrides: Optional[Dict[str, Any]] = None):
        """OpenAI Responses streaming API (structured events)."""
        import json

        base_url = self._get_base_url()
        stop_event = self._get_stop_event(request_overrides)
        stopped = False

        request_payload = {
            "model": self.config.model,
            "input": self._build_openai_responses_input(messages),
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens,
            "stream": True
        }
        if request_overrides:
            if request_overrides.get("input") is not None:
                request_payload["input"] = request_overrides["input"]
            if request_overrides.get("previous_response_id") is not None:
                request_payload["previous_response_id"] = request_overrides["previous_response_id"]
            if request_overrides.get("instructions") is not None:
                request_payload["instructions"] = request_overrides["instructions"]
            if request_overrides.get("tools") is not None:
                request_payload["tools"] = request_overrides["tools"]
            if request_overrides.get("tool_choice") is not None:
                request_payload["tool_choice"] = request_overrides["tool_choice"]
        debug_ctx = self._get_debug_context(request_overrides)

        events: List[Dict[str, Any]] = []
        full_text = ""
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
        last_response: Optional[Dict[str, Any]] = None

        self._apply_reasoning_params(request_payload)

        completed = False
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                should_retry = False
                async with client.stream(
                    "POST",
                    f"{base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_payload
                ) as response:
                    if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                        await response.aread()
                        should_retry = True
                    else:
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            detail_text, detail_json, status = await self._log_http_error(exc)
                            if debug_ctx:
                                error_payload = {"status": status, "message": str(exc)}
                                if detail_json is not None:
                                    error_payload["body"] = detail_json
                                elif detail_text:
                                    error_payload["body"] = detail_text
                                self._save_llm_call(
                                    debug_ctx,
                                    stream=True,
                                    request_payload=request_payload,
                                    response_json={"error": error_payload},
                                    response_text=detail_text or str(exc)
                                )
                            raise
                        async for line in response.aiter_lines():
                            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                stopped = True
                                break
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            events.append(event)
                            event_type = event.get("type", "")

                            if event_type == "response.output_text.delta":
                                delta = event.get("delta", "")
                                if delta:
                                    full_text += delta
                                    yield {"type": "content", "delta": delta}
                                continue

                            if event_type in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                                delta = event.get("delta", "")
                                if delta:
                                    yield {"type": "reasoning", "delta": delta}
                                continue

                            if event_type == "response.function_call_arguments.delta":
                                output_index = event.get("output_index", 0)
                                call = tool_calls_by_index.get(output_index)
                                if not call:
                                    call = {
                                        "index": output_index,
                                        "call_id": event.get("call_id"),
                                        "name": "",
                                        "arguments": ""
                                    }
                                    tool_calls_by_index[output_index] = call
                                if event.get("call_id"):
                                    call["call_id"] = event.get("call_id")
                                delta = event.get("delta", "")
                                call["arguments"] = (call.get("arguments", "") or "") + delta
                                yield {
                                    "type": "tool_call_delta",
                                    "index": output_index,
                                    "id": call.get("call_id"),
                                    "name": call.get("name", ""),
                                    "arguments_delta": delta,
                                    "arguments": call.get("arguments", "")
                                }
                                continue

                            if event_type == "response.function_call_arguments.done":
                                output_index = event.get("output_index", 0)
                                call = tool_calls_by_index.get(output_index)
                                if not call:
                                    call = {"index": output_index, "call_id": event.get("call_id"), "name": "", "arguments": ""}
                                    tool_calls_by_index[output_index] = call
                                if event.get("call_id"):
                                    call["call_id"] = event.get("call_id")
                                if event.get("name"):
                                    call["name"] = event.get("name")
                                if event.get("arguments") is not None:
                                    call["arguments"] = event.get("arguments")
                                yield {
                                    "type": "tool_call_delta",
                                    "index": output_index,
                                    "id": call.get("call_id"),
                                    "name": call.get("name", ""),
                                    "arguments_delta": "",
                                    "arguments": call.get("arguments", "")
                                }
                                continue

                            if event_type in ("response.output_item.added", "response.output_item.done"):
                                item = event.get("item") or event.get("output_item") or {}
                                if isinstance(item, dict) and item.get("type") == "function_call":
                                    output_index = item.get("output_index", event.get("output_index", 0))
                                    call = tool_calls_by_index.get(output_index)
                                    if not call:
                                        call = {"index": output_index, "call_id": None, "name": "", "arguments": ""}
                                        tool_calls_by_index[output_index] = call
                                    if item.get("call_id"):
                                        call["call_id"] = item.get("call_id")
                                    if item.get("name"):
                                        call["name"] = item.get("name")
                                    if item.get("arguments") is not None:
                                        call["arguments"] = item.get("arguments")
                                continue

                            if event_type in ("response.completed", "response.done"):
                                last_response = event.get("response")
                                continue
                        if stopped:
                            pass
                if should_retry:
                    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                        return
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                completed = True
                break

        tool_calls_list = [tool_calls_by_index[idx] for idx in sorted(tool_calls_by_index.keys())]

        if last_response and isinstance(last_response.get("output"), list):
            response_tool_calls = [
                {
                    "index": i,
                    "call_id": item.get("call_id"),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "")
                }
                for i, item in enumerate(last_response.get("output", []))
                if isinstance(item, dict) and item.get("type") == "function_call"
            ]
            if response_tool_calls:
                tool_calls_list = response_tool_calls

        if completed and debug_ctx:
            self._save_llm_call(
                debug_ctx,
                stream=True,
                request_payload=request_payload,
                response_json={"events": events},
                response_text=full_text
            )

        yield {"type": "done", "content": full_text, "tool_calls": tool_calls_list, "raw_events": events, "response": last_response, "stopped": stopped}


def create_llm_client(config: LLMConfig) -> LLMClient:
    """Create LLM client instance."""
    return LLMClient(config)
