"""
ReActAgent - Reasoning + Acting Agent

Implements a ReAct-style loop with tool calling.
- OpenAI: uses native tool calling (tool_calls / function_call)
- Other providers: uses text-based Action/Action Input parsing
"""

import asyncio
import json
import os
import re
import time
import traceback
from datetime import datetime
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple

import httpx
from .base import AgentStrategy, AgentStep
from tools.base import Tool, tool_to_openai_function, tool_to_openai_responses_tool
from tools.config import get_tool_config
from context_estimate import build_context_estimate
from llm_client import LLMTransientError
from app_config import get_app_config
from database import db
from mcp_tools import build_mcp_tool_name, persist_mcp_tool_approval, safe_mcp_tool_name
from tools.mcp_tool import MCPTool
from context_compress import build_history_for_llm, maybe_compress_context, summarize_dialogue


TRUNCATION_MARKER_START = "[TRUNCATED_START]"
TRUNCATION_MARKER_END = "[TRUNCATED_END]"
TRUNCATION_DELAY_CALLS = 2
SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _normalize_tool_name_for_llm(name: Optional[str]) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    if raw.startswith("mcp:") and "/" in raw:
        server_tool = raw[4:]
        server_label, tool = server_tool.split("/", 1)
        return safe_mcp_tool_name(server_label, tool)
    if SAFE_TOOL_NAME_RE.match(raw):
        return raw
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return cleaned or raw


def _get_prompt_truncation_config(request_overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = {}
    if request_overrides and isinstance(request_overrides.get("prompt_truncation"), dict):
        cfg = request_overrides.get("prompt_truncation", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "threshold": int(cfg.get("threshold", 4000) or 4000),
        "head_chars": int(cfg.get("head_chars", 1200) or 1200),
        "tail_chars": int(cfg.get("tail_chars", 800) or 800)
    }


def _pty_stream_debug_enabled() -> bool:
    raw = os.environ.get("PTY_STREAM_DEBUG")
    if raw is not None:
        value = str(raw).strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
        if value in ("0", "false", "no", "off"):
            return False
    raw = os.environ.get("PTY_DEBUG")
    if raw is not None:
        value = str(raw).strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
    dev_value = str(os.environ.get("TAURI_AGENT_DEV", "")).strip().lower()
    if dev_value in ("1", "true", "yes", "on"):
        return True
    env_value = str(os.environ.get("TAURI_AGENT_ENV", "")).strip().lower()
    return env_value in ("dev", "development")


def _pty_stream_log(message: str) -> None:
    if _pty_stream_debug_enabled():
        print(f"[PTY STREAM] {message}")


async def _wait_for_mcp_permission(
    request_id: Optional[int],
    timeout_sec: Optional[float],
    stop_event: Optional[asyncio.Event] = None
) -> str:
    if not request_id:
        return "denied"
    start = time.monotonic()
    while True:
        if stop_event is not None:
            try:
                if getattr(stop_event, "is_set", lambda: False)():
                    return "denied"
            except Exception:
                pass
        try:
            record = db.get_permission_request(request_id)
            if record and record.get("status") and record["status"] != "pending":
                return record["status"]
        except Exception:
            pass

        if timeout_sec is not None and (time.monotonic() - start) >= timeout_sec:
            try:
                db.update_permission_request(request_id, "timeout")
            except Exception:
                pass
            return "timeout"

        await asyncio.sleep(0.5)


def _should_truncate(origin_call_seq: Optional[int], current_call_seq: int, cfg: Dict[str, Any]) -> bool:
    if not cfg.get("enabled"):
        return False
    if origin_call_seq is None:
        return False
    return current_call_seq >= origin_call_seq + TRUNCATION_DELAY_CALLS


def _truncate_text_middle(text: str, cfg: Dict[str, Any]) -> str:
    if text is None:
        return ""
    text_value = str(text)
    threshold = int(cfg.get("threshold", 4000) or 4000)
    if threshold <= 0 or len(text_value) <= threshold:
        return text_value
    head = max(0, int(cfg.get("head_chars", 0) or 0))
    tail = max(0, int(cfg.get("tail_chars", 0) or 0))
    if head + tail >= len(text_value):
        return text_value
    omitted = len(text_value) - head - tail
    head_text = text_value[:head] if head > 0 else ""
    tail_text = text_value[-tail:] if tail > 0 else ""
    return (
        f"{head_text}"
        f"{TRUNCATION_MARKER_START}({omitted} chars omitted){TRUNCATION_MARKER_END}"
        f"{tail_text}"
    )


def _truncate_json_values(value: Any, cfg: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _truncate_text_middle(value, cfg)
    if isinstance(value, list):
        return [_truncate_json_values(item, cfg) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_json_values(item, cfg) for key, item in value.items()}
    return value


def _truncate_json_text(text: Any, cfg: Dict[str, Any], fallback: str = "{}") -> str:
    raw = "" if text is None else str(text)
    raw_stripped = raw.strip()
    if not raw_stripped:
        return fallback
    try:
        parsed = json.loads(raw_stripped)
    except Exception:
        return fallback
    try:
        parsed = _truncate_json_values(parsed, cfg)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        return fallback


def _sanitize_tool_call_arguments(call: Dict[str, Any], current_call_seq: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    new_call = dict(call)
    origin = new_call.pop("__origin_call_seq", None)
    if _should_truncate(origin, current_call_seq, cfg):
        if "arguments" in new_call:
            new_call["arguments"] = _truncate_json_text(new_call.get("arguments", ""), cfg, fallback="{}")
        if "function" in new_call and isinstance(new_call["function"], dict):
            func = dict(new_call["function"])
            if "arguments" in func:
                func["arguments"] = _truncate_json_text(func.get("arguments", ""), cfg, fallback="{}")
            new_call["function"] = func
    if "name" in new_call:
        new_call["name"] = _normalize_tool_name_for_llm(new_call.get("name"))
    if "function" in new_call and isinstance(new_call["function"], dict):
        func = dict(new_call["function"])
        if "name" in func:
            func["name"] = _normalize_tool_name_for_llm(func.get("name"))
        new_call["function"] = func
    return new_call


def _sanitize_messages_for_prompt(
    messages: List[Dict[str, Any]],
    current_call_seq: int,
    cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    for msg in messages:
        new_msg = dict(msg)
        origin = new_msg.pop("__origin_call_seq", None)
        if new_msg.get("role") == "tool" and _should_truncate(origin, current_call_seq, cfg):
            new_msg["content"] = _truncate_text_middle(new_msg.get("content", ""), cfg)
        if new_msg.get("role") == "tool" and "name" in new_msg:
            new_msg["name"] = _normalize_tool_name_for_llm(new_msg.get("name"))
        if "tool_calls" in new_msg and isinstance(new_msg.get("tool_calls"), list):
            new_calls = [
                _sanitize_tool_call_arguments(call, current_call_seq, cfg)
                for call in new_msg["tool_calls"]
            ]
            new_msg["tool_calls"] = new_calls
        sanitized.append(new_msg)
    return sanitized


def _sanitize_response_input(
    input_items: List[Dict[str, Any]],
    current_call_seq: int,
    cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    for item in input_items:
        new_item = dict(item)
        origin = new_item.pop("__origin_call_seq", None)
        if _should_truncate(origin, current_call_seq, cfg):
            item_type = str(new_item.get("type", "") or "")
            if item_type == "function_call":
                new_item["arguments"] = _truncate_json_text(new_item.get("arguments", ""), cfg, fallback="{}")
            elif item_type == "function_call_output":
                new_item["output"] = _truncate_text_middle(new_item.get("output", ""), cfg)
        item_type = str(new_item.get("type", "") or "")
        if item_type == "function_call" and "name" in new_item:
            new_item["name"] = _normalize_tool_name_for_llm(new_item.get("name"))
        sanitized.append(new_item)
    return sanitized


def _render_scratchpad(
    scratchpad: List[Any],
    current_call_seq: int,
    cfg: Dict[str, Any]
) -> str:
    if not scratchpad:
        return "(first iteration)"
    rendered: List[str] = []
    for entry in scratchpad:
        if isinstance(entry, dict):
            text = str(entry.get("text", ""))
            origin = entry.get("origin_call_seq")
            if _should_truncate(origin, current_call_seq, cfg):
                text = _truncate_text_middle(text, cfg)
            rendered.append(text)
        else:
            rendered.append(str(entry))
    return "\n".join(rendered) if rendered else "(first iteration)"


class ReActAgent(AgentStrategy):
    """
    ReAct (Reasoning + Acting) Agent.

    Iteratively:
    - Thinks about the next step
    - Takes an action (uses a tool)
    - Observes the result
    - Continues until reaching a final answer
    """

    def __init__(self, max_iterations: int = 5, system_prompt: Optional[str] = None):
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or ""
    
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
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AgentStep, None]:
        profile = getattr(llm_client.config, "api_profile", None) or getattr(llm_client.config, "api_type", None)
        profile = (profile or "openai").lower()
        if profile in ("openai", "openai_compatible", "deepseek"):
            async for step in self._execute_openai_tool_calling(
                user_input=user_input,
                history=history,
                tools=tools,
                llm_client=llm_client,
                session_id=session_id,
                request_overrides=request_overrides
            ):
                yield step
            return

        async for step in self._execute_text_react(
            user_input=user_input,
            history=history,
            tools=tools,
            llm_client=llm_client,
            session_id=session_id,
            request_overrides=request_overrides
        ):
            yield step

    async def _execute_openai_tool_calling(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]]
    ) -> AsyncGenerator[AgentStep, None]:
        prompt = self.build_prompt(user_input, history, tools, {"tool_calling": True})
        profile = getattr(llm_client.config, "api_profile", None) or getattr(llm_client.config, "api_type", None)
        profile = (profile or "openai").lower()
        prompt_role = "developer" if profile == "openai" else "system"

        history = history or []
        user_content = None
        if request_overrides and request_overrides.get("user_content") is not None:
            user_content = request_overrides.get("user_content")
        user_message_content = user_content if user_content is not None else user_input
        post_user_messages: List[Dict[str, Any]] = []
        if request_overrides and isinstance(request_overrides.get("_post_user_messages"), list):
            for item in request_overrides.get("_post_user_messages") or []:
                if isinstance(item, dict) and item.get("role") and item.get("content") is not None:
                    post_user_messages.append({"role": item.get("role"), "content": item.get("content")})

        context_state: Dict[str, Any] = {}
        if request_overrides and isinstance(request_overrides.get("_context_state"), dict):
            context_state = dict(request_overrides.get("_context_state") or {})
        context_summary = str(context_state.get("summary") or "")
        last_compressed_call_id = context_state.get("last_call_id")
        last_compressed_message_id = context_state.get("last_message_id")
        current_user_message_id = context_state.get("current_user_message_id")
        code_map_prompt = request_overrides.get("_code_map_prompt") if request_overrides else None
        stop_event = request_overrides.get("_stop_event") if request_overrides else None
        current_turn_compresses = 0

        def build_base_messages() -> List[Dict[str, Any]]:
            base_messages: List[Dict[str, Any]] = [{"role": prompt_role, "content": prompt}]
            pre_history: List[Dict[str, Any]] = []
            post_history: List[Dict[str, Any]] = []
            for msg in history or []:
                if isinstance(msg, dict) and msg.get("_after_user"):
                    cleaned = {k: v for k, v in msg.items() if k != "_after_user"}
                    post_history.append(cleaned)
                else:
                    pre_history.append(msg)
            if pre_history:
                base_messages.extend(pre_history)
            base_messages.append({"role": "user", "content": user_message_content})
            if post_history:
                base_messages.extend(post_history)
            if post_user_messages:
                base_messages.extend(post_user_messages)
            return base_messages

        openai_format = "openai_chat_completions"
        if hasattr(llm_client, "_get_format"):
            openai_format = llm_client._get_format()

        if openai_format == "openai_responses":
            openai_tools = [tool_to_openai_responses_tool(t) for t in tools] if tools else []
        else:
            openai_tools = [tool_to_openai_function(t) for t in tools] if tools else []

        base_messages = build_base_messages()
        messages: List[Dict[str, Any]] = list(base_messages)
        dynamic_messages: List[Dict[str, Any]] = []
        dynamic_response_items: List[Dict[str, Any]] = []
        response_input = self._build_responses_input(base_messages) + dynamic_response_items
        current_turn_compresses = 0

        async def compress_current_turn_if_needed(
            current_total_tokens: Optional[int]
        ) -> Optional[AgentStep]:
            nonlocal history, context_summary, last_compressed_call_id, last_compressed_message_id
            nonlocal dynamic_messages, dynamic_response_items, response_input, base_messages, messages
            nonlocal current_turn_compresses

            if not session_id or not current_user_message_id:
                return None
            if current_total_tokens is None:
                return None
            if not dynamic_messages:
                return None
            if current_turn_compresses >= 3:
                return None

            app_config = get_app_config()
            context_cfg = app_config.get("context", {}) if isinstance(app_config, dict) else {}
            if not context_cfg.get("compression_enabled"):
                return None
            try:
                start_pct = int(context_cfg.get("compress_start_pct", 75))
            except (TypeError, ValueError):
                start_pct = 75

            max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
            if max_tokens <= 0:
                return None
            if current_total_tokens < (start_pct / 100.0) * max_tokens:
                return None

            summary_trunc_cfg = {
                "enabled": True,
                "threshold": int(context_cfg.get("long_data_threshold", 2000) or 2000),
                "head_chars": int(context_cfg.get("long_data_head_chars", 600) or 600),
                "tail_chars": int(context_cfg.get("long_data_tail_chars", 400) or 400)
            }
            summary_messages: List[Dict[str, Any]] = []
            for msg in dynamic_messages:
                role = msg.get("role")
                if role == "assistant":
                    content = str(msg.get("content") or "").strip()
                    if content:
                        summary_messages.append({
                            "role": "assistant",
                            "content": _truncate_text_middle(content, summary_trunc_cfg)
                        })
                    tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
                    for call in tool_calls:
                        func = call.get("function") if isinstance(call, dict) else {}
                        name = ""
                        args = ""
                        if isinstance(func, dict):
                            name = func.get("name") or ""
                            args = func.get("arguments") or ""
                        if not name and isinstance(call, dict):
                            name = call.get("name") or "tool"
                        if args:
                            summary_messages.append({
                                "role": "assistant",
                                "content": f"[Tool Call] {name}\n{_truncate_text_middle(args, summary_trunc_cfg)}"
                            })
                elif role == "tool":
                    content = str(msg.get("content") or "").strip()
                    if content:
                        summary_messages.append({
                            "role": "assistant",
                            "content": f"[Tool Result]\n{_truncate_text_middle(content, summary_trunc_cfg)}"
                        })

            if not summary_messages:
                return None

            new_summary = await summarize_dialogue(llm_client, context_summary, summary_messages)
            if not new_summary:
                return None

            context_summary = new_summary
            latest_call_id = db.get_latest_llm_call_id(session_id)
            if latest_call_id:
                last_compressed_call_id = latest_call_id
                last_compressed_message_id = db.get_max_message_id_for_llm_call(
                    session_id,
                    latest_call_id
                )
            try:
                db.update_session_context(session_id, context_summary, last_compressed_call_id)
            except Exception as exc:
                print(f"[Context Compress] Failed to update session context: {exc}")

            if request_overrides is not None:
                request_overrides["_context_state"] = {
                    "summary": context_summary,
                    "last_call_id": last_compressed_call_id,
                    "last_message_id": last_compressed_message_id,
                    "current_user_message_id": current_user_message_id
                }

            dynamic_messages = []
            dynamic_response_items = []
            history = build_history_for_llm(
                session_id,
                last_compressed_message_id,
                current_user_message_id,
                context_summary,
                code_map_prompt,
                trunc_cfg
            )
            base_messages = build_base_messages()
            if openai_format == "openai_responses":
                messages = list(base_messages)
                response_input = self._build_responses_input(base_messages) + dynamic_response_items
            else:
                messages = base_messages + dynamic_messages

            current_turn_compresses += 1
            return AgentStep(
                step_type="observation",
                content="正在进行上下文压缩...",
                metadata={"context_compress": True, "current_turn": True}
            )

        async def refresh_history_if_needed(
            current_total_tokens: Optional[int] = None
        ) -> Tuple[bool, Optional[AgentStep]]:
            nonlocal history, context_summary, last_compressed_call_id, last_compressed_message_id
            if not session_id or not current_user_message_id:
                return False, None

            app_config = get_app_config()
            updated_summary, updated_call_id, updated_message_id, did_compress = await maybe_compress_context(
                session_id=session_id,
                config=llm_client.config,
                app_config=app_config,
                llm_client=llm_client,
                current_summary=context_summary,
                last_compressed_call_id=last_compressed_call_id,
                current_user_message_id=current_user_message_id,
                current_user_text=user_input,
                current_total_tokens=current_total_tokens
            )

            if did_compress:
                context_summary = updated_summary
                last_compressed_call_id = updated_call_id
                last_compressed_message_id = updated_message_id
                try:
                    db.update_session_context(session_id, context_summary, last_compressed_call_id)
                except Exception as exc:
                    print(f"[Context Compress] Failed to update session context: {exc}")

                if request_overrides is not None:
                    request_overrides["_context_state"] = {
                        "summary": context_summary,
                        "last_call_id": last_compressed_call_id,
                        "last_message_id": last_compressed_message_id,
                        "current_user_message_id": current_user_message_id
                    }

                compress_step = AgentStep(
                    step_type="observation",
                    content="正在进行上下文压缩...",
                    metadata={"context_compress": True}
                )

                history = build_history_for_llm(
                    session_id,
                    last_compressed_message_id,
                    current_user_message_id,
                    context_summary,
                    code_map_prompt,
                    trunc_cfg
                )
                return True, compress_step

            return False, None

        async def compress_current_turn_if_needed(
            current_total_tokens: Optional[int]
        ) -> Optional[AgentStep]:
            nonlocal history, context_summary, last_compressed_call_id, last_compressed_message_id
            nonlocal dynamic_messages, dynamic_response_items, response_input, base_messages, messages
            nonlocal current_turn_compresses
            if not session_id or not current_user_message_id:
                return None
            if current_total_tokens is None:
                return None
            if not dynamic_messages:
                return None
            if current_turn_compresses >= 3:
                return None

            app_config = get_app_config()
            context_cfg = app_config.get("context", {}) if isinstance(app_config, dict) else {}
            if not context_cfg.get("compression_enabled"):
                return None
            try:
                start_pct = int(context_cfg.get("compress_start_pct", 75))
            except (TypeError, ValueError):
                start_pct = 75

            max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
            if max_tokens <= 0:
                return None
            if current_total_tokens < (start_pct / 100.0) * max_tokens:
                return None

            summary_trunc_cfg = {
                "enabled": True,
                "threshold": int(context_cfg.get("long_data_threshold", 2000) or 2000),
                "head_chars": int(context_cfg.get("long_data_head_chars", 600) or 600),
                "tail_chars": int(context_cfg.get("long_data_tail_chars", 400) or 400)
            }
            summary_messages: List[Dict[str, Any]] = []
            for msg in dynamic_messages:
                role = msg.get("role")
                if role == "assistant":
                    content = str(msg.get("content") or "").strip()
                    if content:
                        summary_messages.append({
                            "role": "assistant",
                            "content": _truncate_text_middle(content, summary_trunc_cfg)
                        })
                    tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
                    for call in tool_calls:
                        func = call.get("function") if isinstance(call, dict) else {}
                        name = ""
                        args = ""
                        if isinstance(func, dict):
                            name = func.get("name") or ""
                            args = func.get("arguments") or ""
                        if not name and isinstance(call, dict):
                            name = call.get("name") or "tool"
                        if args:
                            summary_messages.append({
                                "role": "assistant",
                                "content": f"[Tool Call] {name}\n{_truncate_text_middle(args, summary_trunc_cfg)}"
                            })
                elif role == "tool":
                    content = str(msg.get("content") or "").strip()
                    if content:
                        summary_messages.append({
                            "role": "assistant",
                            "content": f"[Tool Result]\n{_truncate_text_middle(content, summary_trunc_cfg)}"
                        })

            if not summary_messages:
                return None
            new_summary = await summarize_dialogue(llm_client, context_summary, summary_messages)
            if not new_summary:
                return None

            context_summary = new_summary
            latest_call_id = db.get_latest_llm_call_id(session_id)
            if latest_call_id:
                last_compressed_call_id = latest_call_id
                last_compressed_message_id = db.get_max_message_id_for_llm_call(
                    session_id,
                    latest_call_id
                )
            try:
                db.update_session_context(session_id, context_summary, last_compressed_call_id)
            except Exception as exc:
                print(f"[Context Compress] Failed to update session context: {exc}")

            if request_overrides is not None:
                request_overrides["_context_state"] = {
                    "summary": context_summary,
                    "last_call_id": last_compressed_call_id,
                    "last_message_id": last_compressed_message_id,
                    "current_user_message_id": current_user_message_id
                }

            dynamic_messages = []
            dynamic_response_items = []
            history = build_history_for_llm(
                session_id,
                last_compressed_message_id,
                current_user_message_id,
                context_summary,
                code_map_prompt,
                trunc_cfg
            )
            base_messages = build_base_messages()
            if openai_format == "openai_responses":
                messages = list(base_messages)
                response_input = self._build_responses_input(base_messages) + dynamic_response_items
            else:
                messages = base_messages + dynamic_messages
            current_turn_compresses += 1
            return AgentStep(
                step_type="observation",
                content="正在进行上下文压缩...",
                metadata={"context_compress": True, "current_turn": True}
            )
        trunc_cfg = _get_prompt_truncation_config(request_overrides)
        call_seq = 0
        max_no_answer_attempts = 3

        for iteration in range(self.max_iterations):
            no_answer_attempts = 0
            while True:
                current_call_seq = call_seq
                estimate_emitted = False
                current_total_tokens = None
                try:
                    sanitized_messages = _sanitize_messages_for_prompt(messages, current_call_seq, trunc_cfg)
                    estimate = build_context_estimate(
                        sanitized_messages,
                        tools_payload=openai_tools,
                        max_tokens=None,
                        updated_at=None
                    )
                    current_total_tokens = estimate.get("total")
                except Exception:
                    current_total_tokens = None
                refreshed, compress_step = await refresh_history_if_needed(current_total_tokens)
                if compress_step:
                    yield compress_step
                if refreshed:
                    base_messages = build_base_messages()
                    if openai_format == "openai_responses":
                        messages = list(base_messages)
                        response_input = self._build_responses_input(base_messages) + dynamic_response_items
                    else:
                        messages = base_messages + dynamic_messages
                current_turn_step = await compress_current_turn_if_needed(current_total_tokens)
                if current_turn_step:
                    yield current_turn_step
                llm_overrides = dict(request_overrides) if request_overrides else {}
                if openai_tools:
                    llm_overrides.setdefault("tools", openai_tools)
                    if openai_format != "openai_responses":
                        llm_overrides.setdefault("tool_choice", "auto")

                debug_ctx = self._merge_debug_context(session_id, request_overrides, "react", iteration)
                if debug_ctx:
                    llm_overrides["_debug"] = debug_ctx
                debug_message_id = debug_ctx.get("message_id") if isinstance(debug_ctx, dict) else None
                debug_message_id = debug_message_id if isinstance(debug_message_id, int) else None
                debug_agent_type = debug_ctx.get("agent_type") if isinstance(debug_ctx, dict) else None
                debug_agent_type = str(debug_agent_type or "react")

                if openai_format == "openai_responses":
                    base_overrides = dict(llm_overrides)
                    pending_previous_response_id: Optional[str] = None
                    pending_input = _sanitize_response_input(response_input, current_call_seq, trunc_cfg)
                    approval_rounds = 0
                    mcp_calls: List[Dict[str, Any]] = []
                    response_obj: Optional[Dict[str, Any]] = None
                    response_output_items: List[Dict[str, Any]] = []

                    while True:
                        llm_overrides = dict(base_overrides)
                        llm_overrides["input"] = pending_input
                        if pending_previous_response_id:
                            llm_overrides["previous_response_id"] = pending_previous_response_id

                        max_connect_retries = 3
                        connect_attempt = 0
                        connect_ok = False
                        content_buffer = ""
                        reasoning_buffer = ""
                        tool_calls = []
                        response_output_items = []
                        response_obj = None
                        thought_stream_key = f"assistant_content_{iteration}"
                        reasoning_stream_key = f"assistant_reasoning_{iteration}"
                        stream_mode = "answer"
                        stopped = False

                        while connect_attempt < max_connect_retries:
                            connect_attempt += 1
                            content_buffer = ""
                            reasoning_buffer = ""
                            tool_calls = []
                            response_output_items = []
                            response_obj = None
                            stream_mode = "answer"
                            stopped = False
                            received_any = False

                            if connect_attempt > 1:
                                yield AgentStep(
                                    step_type="thought",
                                    content=f"网络连接中（第{connect_attempt}/{max_connect_retries}次）...",
                                    metadata={"iteration": iteration, "stream_key": thought_stream_key, "network_retry": connect_attempt}
                                )

                            try:
                                sanitized_messages = _sanitize_messages_for_prompt(messages, current_call_seq, trunc_cfg)
                                if not estimate_emitted:
                                    estimate_emitted = True
                                    max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
                                    estimate = build_context_estimate(
                                        sanitized_messages,
                                        tools_payload=openai_tools,
                                        max_tokens=max_tokens,
                                        updated_at=datetime.now().isoformat()
                                    )
                                    yield AgentStep(step_type="context_estimate", content="", metadata=estimate)
                                async for event in llm_client.chat_stream_events(sanitized_messages, llm_overrides if llm_overrides else None):
                                    received_any = True
                                    event_type = event.get("type")
                                    if event_type == "content":
                                        delta = event.get("delta", "")
                                        if delta:
                                            content_buffer += delta
                                            step_type = "answer_delta" if stream_mode == "answer" else "thought_delta"
                                            yield AgentStep(
                                                step_type=step_type,
                                                content=delta,
                                                metadata={"iteration": iteration, "stream_key": thought_stream_key}
                                            )
                                    elif event_type == "reasoning":
                                        delta = event.get("delta", "")
                                        if delta:
                                            reasoning_buffer += delta
                                            yield AgentStep(
                                                step_type="thought_delta",
                                                content=delta,
                                                metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                                            )
                                    elif event_type == "tool_call_delta":
                                        if stream_mode != "thought":
                                            stream_mode = "thought"
                                        call_index = event.get("index", 0)
                                        call_key = f"tool-{iteration}-{call_index}"
                                        tool_name = event.get("name") or ""
                                        args_delta = event.get("arguments_delta", "")
                                        if args_delta or tool_name:
                                            tool_display = tool_name
                                            tool_obj = self._get_tool(tools, tool_name)
                                            if isinstance(tool_obj, MCPTool):
                                                tool_display = tool_obj.display_name
                                            yield AgentStep(
                                                step_type="action_delta",
                                                content=args_delta,
                                                metadata={
                                                    "iteration": iteration,
                                                    "stream_key": call_key,
                                                    "tool": tool_name,
                                                    "tool_display": tool_display,
                                                    "call_index": call_index
                                                }
                                            )
                                    elif event_type == "done":
                                        content_buffer = event.get("content", "") or ""
                                        tool_calls = event.get("tool_calls", []) or []
                                        response_obj = event.get("response") or {}
                                        if isinstance(response_obj, dict):
                                            response_output_items = response_obj.get("output", []) or []
                                        stopped = bool(event.get("stopped"))
                                connect_ok = True
                                break
                            except httpx.ConnectError as e:
                                if received_any:
                                    yield AgentStep(
                                        step_type="error",
                                        content="网络错误：连接中断，请重试。",
                                        metadata={
                                            "error": str(e),
                                            "error_type": "ConnectError",
                                            "suppress_prompt": True,
                                            "transient_error": True
                                        }
                                    )
                                    return
                                if connect_attempt >= max_connect_retries:
                                    yield AgentStep(
                                        step_type="error",
                                        content=f"网络错误：连接失败（已重试{max_connect_retries}次）",
                                        metadata={
                                            "error": str(e),
                                            "error_type": "ConnectError",
                                            "suppress_prompt": True,
                                            "transient_error": True
                                        }
                                    )
                                    return
                                continue
                            except LLMTransientError as e:
                                yield AgentStep(
                                    step_type="error",
                                    content=str(e),
                                    metadata={
                                        "error": str(e),
                                        "error_type": type(e).__name__,
                                        "suppress_prompt": True,
                                        "transient_error": True
                                    }
                                )
                                return

                        if not connect_ok:
                            return

                        call_seq += 1
                        llm_call_id = None
                        if llm_overrides.get("_debug"):
                            llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")

                        if stopped:
                            stopped_text = self._append_stop_note(content_buffer)
                            if llm_call_id:
                                self._update_llm_processed(llm_call_id, {
                                    "stopped_by_user": True,
                                    "content": stopped_text
                                })
                            yield AgentStep(
                                step_type="answer",
                                content=stopped_text,
                                metadata={"agent_type": "react", "iterations": iteration + 1, "stopped_by_user": True}
                            )
                            return

                        response_id = response_obj.get("id") if isinstance(response_obj, dict) else None
                        mcp_calls, mcp_approvals = self._extract_mcp_items(response_output_items)
                        if mcp_approvals:
                            approval_rounds += 1
                            if approval_rounds > 3:
                                yield AgentStep(
                                    step_type="error",
                                    content="MCP approvals exceeded maximum retries.",
                                    metadata={"iteration": iteration, "mcp_approval": True}
                                )
                                return
                            if not response_id:
                                yield AgentStep(
                                    step_type="error",
                                    content="MCP approval requires response id.",
                                    metadata={"iteration": iteration, "mcp_approval": True}
                                )
                                return
                            approval_items = await self._resolve_mcp_approvals(
                                mcp_approvals,
                                session_id=session_id,
                                stop_event=stop_event
                            )
                            if not approval_items:
                                yield AgentStep(
                                    step_type="error",
                                    content="MCP approval failed.",
                                    metadata={"iteration": iteration, "mcp_approval": True}
                                )
                                return
                            pending_previous_response_id = response_id
                            pending_input = approval_items
                            continue
                        break

                    prepared_calls: List[Dict[str, Any]] = []
                    if tool_calls:
                        sanitized_tool_calls: List[Dict[str, Any]] = []
                        for call_index, call in enumerate(tool_calls):
                            call_index = call.get("index", call_index)
                            tool_name = call.get("name")
                            call_id = call.get("call_id") or call.get("id") or f"call_{iteration}_{call_index}"
                            args_text = call.get("arguments", "")
                            _, parse_error = self._safe_json_loads(args_text)
                            sanitized_args = "{}" if parse_error else args_text
                            sanitized_call = dict(call)
                            sanitized_call["arguments"] = sanitized_args
                            sanitized_call["__origin_call_seq"] = current_call_seq
                            sanitized_tool_calls.append(sanitized_call)
                            tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
                            prepared_calls.append({
                                "call_index": call_index,
                                "tool_name": tool_name,
                                "call_id": call_id,
                                "call_key": f"tool-{iteration}-{call_index}",
                                "tool": tool,
                                "tool_input": tool_input,
                                "error_msg": error_msg
                            })
                        tool_calls = sanitized_tool_calls

                    output_items = response_output_items
                    if not output_items:
                        synthetic_items: List[Dict[str, Any]] = []
                        if content_buffer.strip():
                            synthetic_items.append({
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": content_buffer}]
                            })
                        for idx, call in enumerate(tool_calls):
                            call_id = call.get("call_id") or call.get("id") or f"call_{iteration}_{idx}"
                            call["call_id"] = call_id
                            synthetic_items.append({
                                "type": "function_call",
                                "call_id": call_id,
                                "name": call.get("name", ""),
                                "arguments": call.get("arguments", ""),
                                "__origin_call_seq": current_call_seq
                            })
                        output_items = synthetic_items
                    for item in output_items:
                        item_type = item.get("type")
                        if item_type in ("function_call", "function_call_output"):
                            item.setdefault("__origin_call_seq", current_call_seq)

                    if mcp_calls:
                        for mcp_call in mcp_calls:
                            server_label = str(mcp_call.get("server_label") or "").strip()
                            tool_name = str(mcp_call.get("name") or "").strip()
                            args_text = str(mcp_call.get("arguments") or "")
                            display_label = build_mcp_tool_name(server_label or "mcp", tool_name or "tool")
                            safe_label = safe_mcp_tool_name(server_label or "mcp", tool_name or "tool")
                            yield AgentStep(
                                step_type="action",
                                content=f"{display_label}[{args_text}]",
                                metadata={
                                    "tool": safe_label,
                                    "tool_display": display_label,
                                    "input": args_text,
                                    "iteration": iteration,
                                    "mcp": True
                                }
                            )
                            output_text = ""
                            if mcp_call.get("error"):
                                output_text = str(mcp_call.get("error"))
                            elif mcp_call.get("output") is not None:
                                output_text = str(mcp_call.get("output"))
                            else:
                                status = mcp_call.get("status") or "unknown"
                                output_text = f"MCP tool call status: {status}"
                            yield AgentStep(
                                step_type="observation",
                                content=output_text,
                                metadata={"tool": safe_label, "tool_display": display_label, "iteration": iteration, "mcp": True}
                            )
                            success, failure_reason = self._classify_tool_call_result(
                                safe_label,
                                output_text,
                                extra={
                                    "status": mcp_call.get("status"),
                                    "error": mcp_call.get("error")
                                }
                            )
                            self._record_tool_call_history(
                                session_id=session_id,
                                message_id=debug_message_id,
                                agent_type=debug_agent_type,
                                iteration=iteration,
                                tool_name=safe_label,
                                success=success,
                                failure_reason=failure_reason
                            )

                    if tool_calls:
                        if output_items:
                            response_items_for_history = [
                                item for item in output_items
                                if item.get("type") not in ("mcp_call", "mcp_approval_request")
                            ]
                            if response_items_for_history:
                                dynamic_response_items.extend(response_items_for_history)
                        if llm_call_id:
                            self._update_llm_processed(llm_call_id, {
                                "tool_calls": tool_calls,
                                "content": content_buffer
                            })
                        if reasoning_buffer.strip():
                            yield AgentStep(
                                step_type="thought",
                                content=reasoning_buffer,
                                metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                            )
                        if content_buffer.strip():
                            yield AgentStep(
                                step_type="thought",
                                content=content_buffer,
                                metadata={"iteration": iteration, "stream_key": thought_stream_key}
                            )

                        for prepared in prepared_calls:
                            call_index = prepared["call_index"]
                            tool_name = prepared["tool_name"]
                            call_id = prepared["call_id"]
                            call_key = prepared["call_key"]
                            tool = prepared["tool"]
                            tool_input = prepared["tool_input"]
                            error_msg = prepared["error_msg"]
                            tool_label = tool_name
                            tool_meta_name = tool_name
                            if isinstance(tool, MCPTool):
                                tool_label = tool.display_name
                                tool_meta_name = tool.name or tool_name
                            yield AgentStep(
                                step_type="action",
                                content=f"{tool_label}[{tool_input}]",
                                metadata={
                                    "tool": tool_meta_name,
                                    "tool_display": tool_label,
                                    "input": tool_input,
                                    "iteration": iteration,
                                    "stream_key": call_key
                                }
                            )

                            tool_output = ""
                            if error_msg:
                                tool_output = error_msg
                                yield AgentStep(
                                    step_type="observation",
                                    content=tool_output,
                                    metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                                )
                            elif tool is None:
                                tool_output = f"Tool not found: '{tool_name}'"
                                yield AgentStep(
                                    step_type="observation",
                                    content=tool_output,
                                    metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                                )
                            elif str(tool_name or "").lower() == "run_shell":
                                output_holder: Dict[str, str] = {}
                                stream_key = f"{call_key}-obs"
                                async for obs_step in self._stream_run_shell_tool(
                                    tool=tool,
                                    tool_input=tool_input,
                                    tool_name=tool_name,
                                    iteration=iteration,
                                    stream_key=stream_key,
                                    output_holder=output_holder,
                                    stop_event=stop_event
                                ):
                                    yield obs_step
                                tool_output = output_holder.get("output", "")
                            else:
                                tool_output = await self._execute_tool(tool, tool_input)
                                yield AgentStep(
                                    step_type="observation",
                                    content=tool_output,
                                    metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                                )

                            success, failure_reason = self._classify_tool_call_result(
                                tool_meta_name,
                                tool_output,
                                error_msg=error_msg
                            )
                            self._record_tool_call_history(
                                session_id=session_id,
                                message_id=debug_message_id,
                                agent_type=debug_agent_type,
                                iteration=iteration,
                                tool_name=tool_meta_name,
                                success=success,
                                failure_reason=failure_reason
                            )

                            dynamic_response_items.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": tool_output,
                                "__origin_call_seq": current_call_seq
                            })

                        response_input = self._build_responses_input(base_messages) + dynamic_response_items
                        break

                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {"final_answer": content_buffer})

                    if content_buffer.strip():
                        yield AgentStep(
                            step_type="answer",
                            content=content_buffer,
                            metadata={"agent_type": "react", "iterations": iteration + 1, "stream_key": thought_stream_key}
                        )
                        return

                    no_answer_attempts += 1
                    if no_answer_attempts >= max_no_answer_attempts:
                        yield AgentStep(
                            step_type="error",
                            content="LLM returned no content.",
                            metadata={"iteration": iteration}
                        )
                        return
                    continue

                max_connect_retries = 3
                connect_attempt = 0
                connect_ok = False
                thought_stream_key = f"assistant_content_{iteration}"
                reasoning_stream_key = f"assistant_reasoning_{iteration}"

                while connect_attempt < max_connect_retries:
                    connect_attempt += 1
                    content_buffer = ""
                    reasoning_buffer = ""
                    tool_calls = []
                    stream_mode = "answer"
                    stopped = False
                    received_any = False

                    if connect_attempt > 1:
                        yield AgentStep(
                            step_type="thought",
                            content=f"网络连接中（第{connect_attempt}/{max_connect_retries}次）...",
                            metadata={"iteration": iteration, "stream_key": thought_stream_key, "network_retry": connect_attempt}
                        )

                    try:
                        sanitized_messages = _sanitize_messages_for_prompt(messages, current_call_seq, trunc_cfg)
                        if not estimate_emitted:
                            estimate_emitted = True
                            max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
                            estimate = build_context_estimate(
                                sanitized_messages,
                                tools_payload=openai_tools,
                                max_tokens=max_tokens,
                                updated_at=datetime.now().isoformat()
                            )
                            yield AgentStep(step_type="context_estimate", content="", metadata=estimate)
                        async for event in llm_client.chat_stream_events(sanitized_messages, llm_overrides if llm_overrides else None):
                            received_any = True
                            event_type = event.get("type")
                            if event_type == "content":
                                delta = event.get("delta", "")
                                if delta:
                                    content_buffer += delta
                                    step_type = "answer_delta" if stream_mode == "answer" else "thought_delta"
                                    yield AgentStep(
                                        step_type=step_type,
                                        content=delta,
                                        metadata={"iteration": iteration, "stream_key": thought_stream_key}
                                    )
                            elif event_type == "reasoning":
                                delta = event.get("delta", "")
                                if delta:
                                    reasoning_buffer += delta
                                    yield AgentStep(
                                        step_type="thought_delta",
                                        content=delta,
                                        metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                                    )
                            elif event_type == "tool_call_delta":
                                if stream_mode != "thought":
                                    stream_mode = "thought"
                                call_index = event.get("index", 0)
                                call_key = f"tool-{iteration}-{call_index}"
                                tool_name = event.get("name") or ""
                                args_delta = event.get("arguments_delta", "")
                                if args_delta or tool_name:
                                    tool_display = tool_name
                                    tool_obj = self._get_tool(tools, tool_name)
                                    if isinstance(tool_obj, MCPTool):
                                        tool_display = tool_obj.display_name
                                    yield AgentStep(
                                        step_type="action_delta",
                                        content=args_delta,
                                        metadata={
                                            "iteration": iteration,
                                            "stream_key": call_key,
                                            "tool": tool_name,
                                            "tool_display": tool_display,
                                            "call_index": call_index
                                        }
                                    )
                            elif event_type == "done":
                                content_buffer = event.get("content", "") or ""
                                tool_calls = event.get("tool_calls", []) or []
                                stopped = bool(event.get("stopped"))
                        connect_ok = True
                        break
                    except httpx.ConnectError as e:
                        if received_any:
                            yield AgentStep(
                                step_type="error",
                                content="网络错误：连接中断，请重试。",
                                metadata={
                                    "error": str(e),
                                    "error_type": "ConnectError",
                                    "suppress_prompt": True,
                                    "transient_error": True
                                }
                            )
                            return
                        if connect_attempt >= max_connect_retries:
                            yield AgentStep(
                                step_type="error",
                                content=f"网络错误：连接失败（已重试{max_connect_retries}次）",
                                metadata={
                                    "error": str(e),
                                    "error_type": "ConnectError",
                                    "suppress_prompt": True,
                                    "transient_error": True
                                }
                            )
                            return
                        continue
                    except LLMTransientError as e:
                        yield AgentStep(
                            step_type="error",
                            content=str(e),
                            metadata={
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "suppress_prompt": True,
                                "transient_error": True
                            }
                        )
                        return

                if not connect_ok:
                    return

                call_seq += 1
                llm_call_id = None
                if llm_overrides.get("_debug"):
                    llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")

                if stopped:
                    stopped_text = self._append_stop_note(content_buffer)
                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {
                            "stopped_by_user": True,
                            "content": stopped_text
                        })
                    yield AgentStep(
                        step_type="answer",
                        content=stopped_text,
                        metadata={"agent_type": "react", "iterations": iteration + 1, "stopped_by_user": True}
                    )
                    return

                if tool_calls:

                    if reasoning_buffer.strip():
                        yield AgentStep(
                            step_type="thought",
                            content=reasoning_buffer,
                            metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                        )

                    if content_buffer.strip():
                        yield AgentStep(
                            step_type="thought",
                            content=content_buffer,
                            metadata={"iteration": iteration, "stream_key": thought_stream_key}
                        )

                    prepared_calls: List[Dict[str, Any]] = []
                    sanitized_tool_calls: List[Dict[str, Any]] = []
                    for call_index, call in enumerate(tool_calls):
                        call_index = call.get("index", call_index) if isinstance(call, dict) else call_index
                        function = call.get("function", {}) or {}
                        tool_name = function.get("name")
                        args_text = function.get("arguments", "")
                        call_id = call.get("id")
                        _, parse_error = self._safe_json_loads(args_text)
                        sanitized_args = "{}" if parse_error else args_text
                        sanitized_call = dict(call)
                        sanitized_func = dict(function)
                        sanitized_func["arguments"] = sanitized_args
                        sanitized_call["function"] = sanitized_func
                        sanitized_call["__origin_call_seq"] = current_call_seq
                        sanitized_tool_calls.append(sanitized_call)
                        tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
                        prepared_calls.append({
                            "call_index": call_index,
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "call_key": f"tool-{iteration}-{call_index}",
                            "tool": tool,
                            "tool_input": tool_input,
                            "error_msg": error_msg
                        })

                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {
                            "tool_calls": sanitized_tool_calls,
                            "content": content_buffer
                        })

                    dynamic_messages.append({
                        "role": "assistant",
                        "content": content_buffer,
                        "tool_calls": sanitized_tool_calls,
                        "__origin_call_seq": current_call_seq
                    })

                    for prepared in prepared_calls:
                        call_index = prepared["call_index"]
                        tool_name = prepared["tool_name"]
                        call_id = prepared["call_id"]
                        call_key = prepared["call_key"]
                        tool = prepared["tool"]
                        tool_input = prepared["tool_input"]
                        error_msg = prepared["error_msg"]
                        tool_label = tool_name
                        tool_meta_name = tool_name
                        if isinstance(tool, MCPTool):
                            tool_label = tool.display_name
                            tool_meta_name = tool.name or tool_name
                        yield AgentStep(
                            step_type="action",
                            content=f"{tool_label}[{tool_input}]",
                            metadata={
                                "tool": tool_meta_name,
                                "tool_display": tool_label,
                                "input": tool_input,
                                "iteration": iteration,
                                "stream_key": call_key
                            }
                        )
                        tool_output = ""
                        if error_msg:
                            tool_output = error_msg
                            yield AgentStep(
                                step_type="observation",
                                content=tool_output,
                                metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                            )
                        elif tool is None:
                            tool_output = f"Tool not found: '{tool_name}'"
                            yield AgentStep(
                                step_type="observation",
                                content=tool_output,
                                metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                            )
                        elif str(tool_name or "").lower() == "run_shell":
                            output_holder: Dict[str, str] = {}
                            stream_key = f"{call_key}-obs"
                            async for obs_step in self._stream_run_shell_tool(
                                tool=tool,
                                tool_input=tool_input,
                                tool_name=tool_name,
                                iteration=iteration,
                                stream_key=stream_key,
                                output_holder=output_holder,
                                stop_event=stop_event
                            ):
                                yield obs_step
                            tool_output = output_holder.get("output", "")
                        else:
                            tool_output = await self._execute_tool(tool, tool_input)
                            yield AgentStep(
                                step_type="observation",
                                content=tool_output,
                                metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                            )

                        success, failure_reason = self._classify_tool_call_result(
                            tool_meta_name,
                            tool_output,
                            error_msg=error_msg
                        )
                        self._record_tool_call_history(
                            session_id=session_id,
                            message_id=debug_message_id,
                            agent_type=debug_agent_type,
                            iteration=iteration,
                            tool_name=tool_meta_name,
                            success=success,
                            failure_reason=failure_reason
                        )

                    dynamic_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_output,
                        "__origin_call_seq": current_call_seq
                    })
                    messages = base_messages + dynamic_messages

                    break

                if llm_call_id:
                    self._update_llm_processed(llm_call_id, {"final_answer": content_buffer})

                if reasoning_buffer.strip():
                    yield AgentStep(
                        step_type="thought",
                        content=reasoning_buffer,
                        metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                    )

                if content_buffer.strip():
                    yield AgentStep(
                        step_type="answer",
                        content=content_buffer,
                        metadata={"agent_type": "react", "iterations": iteration + 1, "stream_key": thought_stream_key}
                    )
                    return

                no_answer_attempts += 1
                if no_answer_attempts >= max_no_answer_attempts:
                    yield AgentStep(
                        step_type="error",
                        content="LLM returned no content.",
                        metadata={"iteration": iteration}
                    )
                    return

        yield AgentStep(
            step_type="answer",
            content="Sorry, I could not complete the task within the limit.",
            metadata={"agent_type": "react", "iterations": self.max_iterations, "max_iterations_reached": True}
        )

    async def _execute_text_react(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]]
    ) -> AsyncGenerator[AgentStep, None]:
        scratchpad: List[Dict[str, Any]] = []
        history = history or []
        user_content = None
        if request_overrides and request_overrides.get("user_content") is not None:
            user_content = request_overrides.get("user_content")
        trunc_cfg = _get_prompt_truncation_config(request_overrides)
        call_seq = 0
        stop_event = request_overrides.get("_stop_event") if request_overrides else None

        context_state: Dict[str, Any] = {}
        if request_overrides and isinstance(request_overrides.get("_context_state"), dict):
            context_state = dict(request_overrides.get("_context_state") or {})
        context_summary = str(context_state.get("summary") or "")
        last_compressed_call_id = context_state.get("last_call_id")
        last_compressed_message_id = context_state.get("last_message_id")
        current_user_message_id = context_state.get("current_user_message_id")
        code_map_prompt = request_overrides.get("_code_map_prompt") if request_overrides else None

        async def refresh_history_if_needed(
            current_total_tokens: Optional[int] = None
        ) -> Tuple[bool, Optional[AgentStep]]:
            nonlocal history, context_summary, last_compressed_call_id, last_compressed_message_id
            if not session_id or not current_user_message_id:
                return False, None

            app_config = get_app_config()
            updated_summary, updated_call_id, updated_message_id, did_compress = await maybe_compress_context(
                session_id=session_id,
                config=llm_client.config,
                app_config=app_config,
                llm_client=llm_client,
                current_summary=context_summary,
                last_compressed_call_id=last_compressed_call_id,
                current_user_message_id=current_user_message_id,
                current_user_text=user_input,
                current_total_tokens=current_total_tokens
            )

            if did_compress:
                context_summary = updated_summary
                last_compressed_call_id = updated_call_id
                last_compressed_message_id = updated_message_id
                try:
                    db.update_session_context(session_id, context_summary, last_compressed_call_id)
                except Exception as exc:
                    print(f"[Context Compress] Failed to update session context: {exc}")

                if request_overrides is not None:
                    request_overrides["_context_state"] = {
                        "summary": context_summary,
                        "last_call_id": last_compressed_call_id,
                        "last_message_id": last_compressed_message_id,
                        "current_user_message_id": current_user_message_id
                    }

                compress_step = AgentStep(
                    step_type="observation",
                    content="正在进行上下文压缩...",
                    metadata={"context_compress": True}
                )

                history = build_history_for_llm(
                    session_id,
                    last_compressed_message_id,
                    current_user_message_id,
                    context_summary,
                    code_map_prompt,
                    trunc_cfg
                )
                return True, compress_step

            return False, None

        for iteration in range(self.max_iterations):
            current_call_seq = call_seq
            prompt = self.build_prompt(user_input, history, tools, {
                "scratchpad": scratchpad,
                "iteration": iteration,
                "tool_calling": False,
                "call_seq": current_call_seq,
                "prompt_truncation": trunc_cfg
            })

            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content if user_content is not None else user_input}
            ]
            current_total_tokens = None
            try:
                estimate_preview = build_context_estimate(
                    messages,
                    tools_payload=None,
                    max_tokens=None,
                    updated_at=None
                )
                current_total_tokens = estimate_preview.get("total")
            except Exception:
                current_total_tokens = None

            refreshed, compress_step = await refresh_history_if_needed(current_total_tokens)
            if compress_step:
                yield compress_step
            current_turn_step = await compress_current_turn_if_needed(current_total_tokens)
            if current_turn_step:
                yield current_turn_step
                prompt = self.build_prompt(user_input, history, tools, {
                    "scratchpad": scratchpad,
                    "iteration": iteration,
                    "tool_calling": False,
                    "call_seq": current_call_seq,
                    "prompt_truncation": trunc_cfg
                })
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content if user_content is not None else user_input}
                ]
            if refreshed:
                prompt = self.build_prompt(user_input, history, tools, {
                    "scratchpad": scratchpad,
                    "iteration": iteration,
                    "tool_calling": False,
                    "call_seq": current_call_seq,
                    "prompt_truncation": trunc_cfg
                })
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content if user_content is not None else user_input}
                ]

            try:
                llm_overrides = dict(request_overrides) if request_overrides else {}
                debug_ctx = self._merge_debug_context(session_id, request_overrides, "react", iteration)
                if debug_ctx:
                    llm_overrides["_debug"] = debug_ctx
                debug_message_id = debug_ctx.get("message_id") if isinstance(debug_ctx, dict) else None
                debug_message_id = debug_message_id if isinstance(debug_message_id, int) else None
                debug_agent_type = debug_ctx.get("agent_type") if isinstance(debug_ctx, dict) else None
                debug_agent_type = str(debug_agent_type or "react")

                max_tokens = getattr(llm_client.config, "max_context_tokens", 0) or 0
                estimate = build_context_estimate(
                    messages,
                    tools_payload=None,
                    max_tokens=max_tokens,
                    updated_at=datetime.now().isoformat()
                )
                yield AgentStep(step_type="context_estimate", content="", metadata=estimate)

                response = await llm_client.chat(messages, llm_overrides if llm_overrides else None)
                llm_output = response.get("content", "")

            except Exception as e:
                suppress_prompt = isinstance(e, LLMTransientError)
                metadata = {"iteration": iteration, "error": str(e), "traceback": traceback.format_exc()}
                if suppress_prompt:
                    metadata["suppress_prompt"] = True
                    metadata["transient_error"] = True
                    metadata["error_type"] = type(e).__name__
                yield AgentStep(
                    step_type="error",
                    content=f"LLM call failed: {str(e)}",
                    metadata=metadata
                )
                return

            thought, action, action_input, final_answer = self._parse_reaction(llm_output)
            call_seq += 1
            llm_call_id = response.get("llm_call_id")
            if llm_call_id:
                self._update_llm_processed(llm_call_id, {
                    "thought": thought,
                    "action": action,
                    "action_input": action_input,
                    "final_answer": final_answer
                })

            if final_answer:
                yield AgentStep(
                    step_type="answer",
                    content=final_answer,
                    metadata={"agent_type": "react", "iterations": iteration + 1, "scratchpad": scratchpad}
                )
                return

            if thought:
                yield AgentStep(
                    step_type="thought",
                    content=thought,
                    metadata={"iteration": iteration}
                )
                scratchpad.append({"text": f"Thought: {thought}", "origin_call_seq": current_call_seq})

            if action and action_input:
                tool = self._get_tool(tools, action)
                tool_label = action
                tool_meta_name = action
                if isinstance(tool, MCPTool):
                    tool_label = tool.display_name
                    tool_meta_name = tool.name or action
                yield AgentStep(
                    step_type="action",
                    content=f"{tool_label}[{action_input}]",
                    metadata={"tool": tool_meta_name, "tool_display": tool_label, "input": action_input, "iteration": iteration}
                )
                scratchpad.append({"text": f"Action: {action}", "origin_call_seq": current_call_seq})
                scratchpad.append({"text": f"Action Input: {action_input}", "origin_call_seq": current_call_seq})

                if tool:
                    try:
                        observation = ""
                        if str(action or "").lower() == "run_shell":
                            output_holder: Dict[str, str] = {}
                            stream_key = f"text-tool-{iteration}-{call_seq}"
                            async for obs_step in self._stream_run_shell_tool(
                                tool=tool,
                                tool_input=action_input,
                                tool_name=action,
                                iteration=iteration,
                                stream_key=stream_key,
                                output_holder=output_holder,
                                stop_event=stop_event
                            ):
                                yield obs_step
                            observation = output_holder.get("output", "")
                        else:
                            observation = await tool.execute(action_input)
                            yield AgentStep(
                                step_type="observation",
                                content=observation,
                                metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                            )
                        scratchpad.append({"text": f"Observation: {observation}", "origin_call_seq": current_call_seq})
                        success, failure_reason = self._classify_tool_call_result(
                            tool_meta_name,
                            observation
                        )
                        self._record_tool_call_history(
                            session_id=session_id,
                            message_id=debug_message_id,
                            agent_type=debug_agent_type,
                            iteration=iteration,
                            tool_name=tool_meta_name,
                            success=success,
                            failure_reason=failure_reason
                        )
                    except Exception as e:
                        error_msg = f"Tool execution failed: {str(e)}"
                        yield AgentStep(
                            step_type="observation",
                            content=error_msg,
                            metadata={"tool": tool_meta_name, "tool_display": tool_label, "error": str(e), "iteration": iteration}
                        )
                        scratchpad.append({"text": f"Observation: {error_msg}", "origin_call_seq": current_call_seq})
                        success, failure_reason = self._classify_tool_call_result(
                            tool_meta_name,
                            error_msg,
                            error_msg=error_msg
                        )
                        self._record_tool_call_history(
                            session_id=session_id,
                            message_id=debug_message_id,
                            agent_type=debug_agent_type,
                            iteration=iteration,
                            tool_name=tool_meta_name,
                            success=success,
                            failure_reason=failure_reason
                        )
                else:
                    error_msg = f"Tool not found: '{action}'"
                    yield AgentStep(
                        step_type="error",
                        content=error_msg,
                        metadata={"tool": tool_meta_name, "tool_display": tool_label, "iteration": iteration}
                    )
                    scratchpad.append({"text": f"Observation: {error_msg}", "origin_call_seq": current_call_seq})
                    success, failure_reason = self._classify_tool_call_result(
                        tool_meta_name,
                        error_msg,
                        error_msg=error_msg
                    )
                    self._record_tool_call_history(
                        session_id=session_id,
                        message_id=debug_message_id,
                        agent_type=debug_agent_type,
                        iteration=iteration,
                        tool_name=tool_meta_name,
                        success=success,
                        failure_reason=failure_reason
                    )
            else:
                yield AgentStep(
                    step_type="thought",
                    content="(Agent could not determine next action)",
                    metadata={"iteration": iteration, "warning": "no_action"}
                )

        yield AgentStep(
            step_type="answer",
            content="Sorry, I could not complete the task within the limit.",
            metadata={"agent_type": "react", "iterations": self.max_iterations, "max_iterations_reached": True}
        )

    def build_prompt(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        tool_names = ", ".join([tool.name for tool in tools]) if tools else "(no tools available)"
        tool_calling = bool(additional_context and additional_context.get("tool_calling"))
        scratchpad = additional_context.get("scratchpad", []) if additional_context else []
        current_call_seq = int(additional_context.get("call_seq", 0)) if additional_context else 0
        trunc_cfg = additional_context.get("prompt_truncation") if additional_context else None
        if not isinstance(trunc_cfg, dict):
            trunc_cfg = {"enabled": False}
        scratchpad_text = _render_scratchpad(scratchpad, current_call_seq, trunc_cfg)

        base_prompt = (self.system_prompt or "").strip()
        sections: List[str] = []
        if base_prompt:
            sections.append(base_prompt)

        if tool_calling:
            sections.append(
                "You are a reasoning + acting assistant. Use tools via function/tool calling when needed.\n\n"
                "## Tools\n"
                f"Available tool names: {tool_names}\n"
                "Tool definitions are provided separately via the API tools field.\n\n"
                "Guidelines:\n"
                "- If a tool is needed, call it with JSON arguments that match its schema.\n"
                "- Prefer rg for searching file contents.\n"
                "- Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\n"
                "- apply_patch format (strict):\n"
                "  *** Begin Patch\n"
                "  *** Update File: path\n"
                "  @@\n"
                "  - old line\n"
                "  + new line\n"
                "  *** End Patch\n"
                "- Each change line must start with + or -, and context lines must be included under @@ hunks.\n"
                "- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n"
                "- apply_patch matches by context; if the match is not unique, request more surrounding context.\n"
                "- If apply_patch fails due to context, ask for more context and retry.\n"
                "- If no tool is needed, answer directly."
            )
            return "\n\n".join(sections).strip()

        sections.append(
            "You are a reasoning + acting assistant. Follow the format exactly.\n\n"
            "## Tools\n"
            f"Available tool names: {tool_names}\n"
            "Tool definitions are provided separately via the API tools field.\n"
            "Guidelines:\n"
            "- Prefer rg for searching file contents.\n"
            "- Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\n"
            "- apply_patch format (strict):\n"
            "  *** Begin Patch\n"
            "  *** Update File: path\n"
            "  @@\n"
            "  - old line\n"
            "  + new line\n"
            "  *** End Patch\n"
            "- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n"
            "- If apply_patch context is not unique, request more surrounding context.\n"
            "- If apply_patch fails due to context, request more context and retry.\n\n"
            "## Output Format (strict)\n"
            "Thought: <your reasoning>\n"
            "Action: <tool name>\n"
            "Action Input: <tool input>\n\n"
            "System will reply with:\n"
            "Observation: <tool output>\n\n"
            "Repeat as needed, then finish with:\n"
            "Thought: I now know the final answer.\n"
            "Final Answer: <your final answer>"
        )
        if history:
            history_lines: List[str] = []
            for msg in history:
                role = (msg.get("role") or "user").lower()
                content = str(msg.get("content") or "").strip()
                if not content:
                    continue
                if role == "user":
                    label = "User"
                elif role == "assistant":
                    label = "Assistant"
                else:
                    label = role.capitalize()
                content = _truncate_text_middle(content, trunc_cfg)
                history_lines.append(f"{label}: {content}")
            if history_lines:
                sections.append("## History\n" + "\n".join(history_lines))
        sections.append(f"## Scratchpad\n{scratchpad_text}")
        return "\n\n".join(sections).strip()

    def _parse_reaction(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        thought_match = re.search(r"Thought:\s*(.+?)(?=\n(?:Action|Final Answer):|$)", text, re.DOTALL | re.IGNORECASE)
        action_match = re.search(r"Action:\s*(\w+)", text, re.IGNORECASE)
        action_input_match = re.search(r"Action Input:\s*(.+?)(?=\nObservation:|$)", text, re.DOTALL | re.IGNORECASE)
        final_answer_match = re.search(r"Final Answer:\s*(.+?)$", text, re.DOTALL | re.IGNORECASE)

        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        action_input = action_input_match.group(1).strip() if action_input_match else None
        final_answer = final_answer_match.group(1).strip() if final_answer_match else None

        return thought, action, action_input, final_answer

    def _append_stop_note(self, content: str) -> str:
        note = "[用户主动停止输出]"
        base = (content or "").rstrip()
        if not base:
            return note
        if base.endswith(note):
            return base
        return f"{base}\n\n{note}"

    def _extract_mcp_items(
        self,
        output_items: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        mcp_calls: List[Dict[str, Any]] = []
        approvals: List[Dict[str, Any]] = []
        for item in output_items or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "mcp_call":
                mcp_calls.append(item)
            elif item_type == "mcp_approval_request":
                approvals.append(item)
        return mcp_calls, approvals

    async def _resolve_mcp_approvals(
        self,
        approvals: List[Dict[str, Any]],
        session_id: Optional[str],
        stop_event: Optional[asyncio.Event]
    ) -> Optional[List[Dict[str, Any]]]:
        if not approvals:
            return []
        try:
            timeout_sec = float(get_tool_config().get("shell", {}).get("permission_timeout_sec", 300))
        except Exception:
            timeout_sec = 300.0

        response_items: List[Dict[str, Any]] = []
        for approval in approvals:
            if not isinstance(approval, dict):
                continue
            approval_id = approval.get("id") or approval.get("approval_request_id")
            server_label = str(approval.get("server_label") or "").strip()
            tool_name = str(approval.get("name") or "").strip()
            args_text = str(approval.get("arguments") or "")
            if not approval_id:
                return None

            display_label = build_mcp_tool_name(server_label or "mcp", tool_name or "tool")
            reason = f"MCP tool approval: {display_label}"
            if args_text:
                trunc_cfg = {"enabled": True, "threshold": 800, "head_chars": 500, "tail_chars": 200}
                reason = f"{reason}\nArgs: {_truncate_text_middle(args_text, trunc_cfg)}"

            request_id = None
            try:
                request_id = db.create_permission_request(
                    tool_name=display_label,
                    action="mcp_approval",
                    path=display_label,
                    reason=reason,
                    session_id=session_id or "global"
                )
            except Exception:
                request_id = None

            status = await _wait_for_mcp_permission(request_id, timeout_sec, stop_event)
            approve = status in ("approved", "approved_once")
            if approve and status == "approved" and server_label and tool_name:
                persist_mcp_tool_approval(server_label, tool_name)

            response_item: Dict[str, Any] = {
                "type": "mcp_approval_response",
                "approval_request_id": approval_id,
                "approve": approve
            }
            if status in ("denied", "timeout"):
                response_item["reason"] = "User denied" if status == "denied" else "Timed out"
            response_items.append(response_item)

        return response_items

    def _get_tool(self, tools: List[Tool], name: Optional[str]) -> Optional[Tool]:
        if not name:
            return None
        return next((t for t in tools if t.name.lower() == name.lower()), None)

    def _safe_json_loads(self, value: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not value:
            return {}, None
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return data, None
            return None, "Tool arguments must be a JSON object."
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON arguments: {e}"

    def _short_failure_reason(self, value: Optional[str], fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        first_line = text.splitlines()[0].strip() if "\n" in text else text
        if not first_line:
            first_line = fallback
        if len(first_line) > 200:
            return f"{first_line[:197]}..."
        return first_line

    def _extract_last_exit_code(self, output_text: str) -> Optional[int]:
        if not output_text:
            return None
        matches = re.findall(r"exit_code\s*=\s*(-?\d+)", output_text)
        if not matches:
            return None
        try:
            return int(matches[-1])
        except (TypeError, ValueError):
            return None

    def _classify_tool_call_result(
        self,
        tool_name: Optional[str],
        tool_output: Optional[str],
        error_msg: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str]]:
        failed_statuses = {"failed", "error", "denied", "cancelled", "timeout"}
        extra_payload = extra or {}
        output_text = str(tool_output or "")
        output_stripped = output_text.strip()
        normalized_tool_name = str(tool_name or "").strip().lower()

        if error_msg:
            return False, self._short_failure_reason(error_msg, "tool_error")

        mcp_error = extra_payload.get("error")
        if mcp_error:
            return False, self._short_failure_reason(str(mcp_error), "mcp_error")

        mcp_status = str(extra_payload.get("status") or "").strip().lower()
        if mcp_status in failed_statuses:
            return False, f"mcp_status={mcp_status}"

        if output_stripped.startswith("Tool execution failed:"):
            return False, self._short_failure_reason(output_stripped, "tool_execution_failed")
        if output_stripped.startswith("Tool not found:"):
            return False, self._short_failure_reason(output_stripped, "tool_not_found")
        if output_stripped.startswith("Invalid JSON arguments:"):
            return False, "invalid_json_arguments"
        if output_stripped.startswith("Tool arguments must be a JSON object."):
            return False, "tool_arguments_not_object"

        if output_stripped.startswith("Permission denied."):
            return False, "permission_denied"
        if output_stripped.startswith("Permission request timed out."):
            return False, "permission_request_timed_out"
        if output_stripped.startswith("Permission required."):
            return False, "permission_required"

        if "[用户主动停止输出]" in output_stripped:
            return False, "stopped_by_user"

        if normalized_tool_name == "run_shell":
            exit_code = self._extract_last_exit_code(output_text)
            if exit_code is not None and exit_code != 0:
                return False, f"exit_code={exit_code}"

        if normalized_tool_name == "apply_patch":
            try:
                payload = json.loads(output_stripped) if output_stripped else None
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("ok") is False:
                reason = payload.get("error")
                if reason:
                    return False, self._short_failure_reason(str(reason), "apply_patch_ok_false")
                return False, "apply_patch_ok_false"

        return True, None

    def _record_tool_call_history(
        self,
        session_id: Optional[str],
        message_id: Optional[int],
        agent_type: Optional[str],
        iteration: Optional[int],
        tool_name: Optional[str],
        success: bool,
        failure_reason: Optional[str] = None
    ) -> None:
        if not session_id:
            return
        normalized_tool_name = str(tool_name or "").strip() or "unknown"
        normalized_reason = self._short_failure_reason(failure_reason, "failed") if not success else None
        try:
            db.save_session_tool_call_history(
                session_id=session_id,
                message_id=message_id,
                agent_type=agent_type,
                iteration=iteration,
                tool_name=normalized_tool_name,
                success=success,
                failure_reason=normalized_reason
            )
        except Exception:
            pass

    def _split_shell_output(self, output: str) -> Tuple[str, str]:
        if not output:
            return "", ""
        normalized = output.replace("\r\n", "\n")
        if "\n" in normalized:
            header, body = normalized.split("\n", 1)
        else:
            header, body = normalized, ""
        return header.strip(), body

    def _parse_shell_header(self, header_line: str) -> Dict[str, str]:
        header_line = (header_line or "").strip()
        if not header_line:
            return {}
        tokens: List[str] = []
        if "]" in header_line:
            prefix, rest = header_line.split("]", 1)
            if prefix.startswith("["):
                tokens.extend(prefix[1:].strip().split())
            else:
                tokens.extend(prefix.strip().split())
            if rest:
                tokens.extend(rest.strip().split())
        else:
            trimmed = header_line
            if trimmed.startswith("[") and trimmed.endswith("]"):
                trimmed = trimmed[1:-1]
            tokens.extend(trimmed.strip().split())
        parsed: Dict[str, str] = {}
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key:
                parsed[key] = value
        return parsed

    def _extract_tool_input(self, tool: Tool, args: Dict[str, Any]) -> str:
        if not tool.parameters:
            return json.dumps(args) if args else ""
        if len(tool.parameters) == 1:
            key = tool.parameters[0].name
            value = args.get(key, "")
            if isinstance(tool, MCPTool):
                return json.dumps(args) if args else ""
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return str(value)
        return json.dumps(args)

    def _prepare_tool_call(self, tools: List[Tool], tool_name: Optional[str], args_text: str) -> Tuple[Optional[Tool], str, Optional[str]]:
        tool = self._get_tool(tools, tool_name)
        args, parse_error = self._safe_json_loads(args_text)

        if tool is None:
            return None, "", f"Tool not found: '{tool_name}'"

        if parse_error:
            return tool, "", parse_error

        tool_input = self._extract_tool_input(tool, args or {})
        return tool, tool_input, None

    async def _execute_tool(self, tool: Tool, tool_input: str) -> str:
        try:
            return await tool.execute(tool_input)
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    async def _stream_run_shell_tool(
        self,
        tool: Tool,
        tool_input: str,
        tool_name: str,
        iteration: int,
        stream_key: str,
        output_holder: Dict[str, str],
        stop_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[AgentStep, None]:
        def _stop_requested() -> bool:
            if stop_event is None:
                return False
            try:
                return bool(getattr(stop_event, "is_set", lambda: False)())
            except Exception:
                return False

        log_interval_sec = 5.0
        try:
            log_interval_sec = float(os.environ.get("PTY_STREAM_LOG_INTERVAL_SEC", "5") or "5")
        except (TypeError, ValueError):
            log_interval_sec = 5.0
        if log_interval_sec <= 0:
            log_interval_sec = 5.0
        read_timeout_sec: Optional[float] = None
        try:
            raw_timeout = os.environ.get("PTY_STREAM_READ_TIMEOUT_SEC")
            if raw_timeout is not None and str(raw_timeout).strip() != "":
                read_timeout_sec = float(raw_timeout)
        except (TypeError, ValueError):
            read_timeout_sec = None
        if read_timeout_sec is not None and read_timeout_sec <= 0:
            read_timeout_sec = None
        last_log_at = time.monotonic()

        _pty_stream_log(
            f"start stream_key={stream_key} tool={tool_name} iteration={iteration}"
        )

        args, parse_error = self._safe_json_loads(tool_input)
        if parse_error or not isinstance(args, dict):
            tool_output = parse_error or "Tool arguments must be a JSON object."
            output_holder["output"] = tool_output
            yield AgentStep(
                step_type="observation",
                content=tool_output,
                metadata={"tool": tool_name, "iteration": iteration}
            )
            _pty_stream_log(
                f"parse_error stream_key={stream_key} error={tool_output}"
            )
            return
        if _stop_requested():
            stopped_output = self._append_stop_note("")
            output_holder["output"] = stopped_output
            yield AgentStep(
                step_type="observation",
                content=stopped_output,
                metadata={
                    "tool": tool_name,
                    "iteration": iteration,
                    "stream_key": stream_key,
                    "stopped_by_user": True
                }
            )
            _pty_stream_log(
                f"stopped_before_start stream_key={stream_key}"
            )
            return

        action = str(args.get("action") or "").strip().lower()
        mode = str(args.get("mode") or "auto").strip().lower()
        if mode not in ("auto", "oneshot", "persistent"):
            mode = "auto"
        pty_requested = args.get("pty")
        _pty_stream_log(
            f"args stream_key={stream_key} action={action or 'none'} mode={mode} pty_requested={pty_requested}"
        )
        if action or args.get("pty_id"):
            tool_output = await self._execute_tool(tool, tool_input)
            output_holder["output"] = tool_output
            yield AgentStep(
                step_type="observation",
                content=tool_output,
                metadata={"tool": tool_name, "iteration": iteration}
            )
            _pty_stream_log(
                f"action_direct stream_key={stream_key} action={action or 'none'}"
            )
            return

        if mode == "persistent":
            persistent_args = dict(args)
            persistent_args["mode"] = "persistent"
            command_text = str(persistent_args.get("command") or "")
            tool_output = await self._execute_tool(tool, json.dumps(persistent_args))
            header_line, _ = self._split_shell_output(tool_output)
            header_data = self._parse_shell_header(header_line)
            waiting_input = header_data.get("waiting_input") == "true"
            wait_reason = str(header_data.get("wait_reason") or "").strip() or None
            metadata: Dict[str, Any] = {
                "tool": tool_name,
                "iteration": iteration,
                "stream_key": stream_key
            }
            if command_text:
                metadata["command"] = command_text
            metadata["waiting_input"] = waiting_input
            if wait_reason:
                metadata["wait_reason"] = wait_reason
            output_holder["output"] = tool_output
            yield AgentStep(
                step_type="observation",
                content=tool_output,
                metadata=metadata
            )
            _pty_stream_log(
                f"persistent_direct stream_key={stream_key} waiting_input={str(waiting_input).lower()}"
            )
            return

        command = args.get("command")
        if not command:
            tool_output = await self._execute_tool(tool, tool_input)
            output_holder["output"] = tool_output
            yield AgentStep(
                step_type="observation",
                content=tool_output,
                metadata={"tool": tool_name, "iteration": iteration}
            )
            return

        command_text = str(command)
        shell_cfg = get_tool_config().get("shell", {}) or {}
        marker_cfg = shell_cfg.get("pty_completion_marker_enabled", True)
        if isinstance(marker_cfg, str):
            marker_enabled = marker_cfg.strip().lower() not in ("0", "false", "no", "off")
        else:
            marker_enabled = bool(marker_cfg)
        # Windows-first rollout: keep non-Windows behavior unchanged.
        marker_enabled = marker_enabled and os.name == "nt"

        start_args = dict(args)
        start_args["mode"] = "persistent"
        start_args.pop("action", None)
        start_args.pop("pty_id", None)
        start_args.pop("cursor", None)
        start_args.pop("track_completion", None)
        start_args.pop("completion_key", None)
        if "idle_timeout" not in start_args:
            start_args["idle_timeout"] = 120000
        start_args["command"] = "" if marker_enabled else command_text

        if _stop_requested():
            stopped_output = self._append_stop_note("")
            output_holder["output"] = stopped_output
            yield AgentStep(
                step_type="observation",
                content=stopped_output,
                metadata={
                    "tool": tool_name,
                    "iteration": iteration,
                    "stream_key": stream_key,
                    "stopped_by_user": True,
                    "command": str(command)
                }
            )
            _pty_stream_log(
                f"stopped_before_launch stream_key={stream_key} command={command_text}"
            )
            return

        start_output = await self._execute_tool(tool, json.dumps(start_args))
        header_line, body = self._split_shell_output(start_output)
        header_data = self._parse_shell_header(header_line)
        pty_id = header_data.get("pty_id")
        status = header_data.get("status")
        waiting_input = header_data.get("waiting_input") == "true"
        wait_reason = str(header_data.get("wait_reason") or "").strip() or None
        _pty_stream_log(
            f"started stream_key={stream_key} pty_id={pty_id} status={status} header={header_line}"
        )
        if not header_line or not pty_id:
            output_holder["output"] = start_output
            yield AgentStep(
                step_type="observation",
                content=start_output,
                metadata={"tool": tool_name, "iteration": iteration}
            )
            _pty_stream_log(
                f"start_failed stream_key={stream_key} output={start_output[:120] if start_output else ''}"
            )
            return
        completion_key: Optional[str] = None
        if marker_enabled and status != "exited":
            send_payload: Dict[str, Any] = {
                "action": "send",
                "pty_id": pty_id,
                "stdin": command_text,
                "track_completion": True
            }
            send_output = await self._execute_tool(tool, json.dumps(send_payload))
            send_header, _ = self._split_shell_output(send_output)
            if not send_header or not send_header.startswith("["):
                await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
                output_holder["output"] = send_output
                yield AgentStep(
                    step_type="observation",
                    content=send_output,
                    metadata={"tool": tool_name, "iteration": iteration, "stream_key": stream_key, "command": command_text}
                )
                return
            send_header_data = self._parse_shell_header(send_header)
            completion_key = send_header_data.get("completion_key")
            if not completion_key:
                marker_enabled = False
                _pty_stream_log(
                    f"completion_key_missing stream_key={stream_key} pty_id={pty_id}, fallback=status"
                )

        if body.strip() == "(no output)" and status != "exited":
            body = ""

        command_prefix = f"$ {command_text}" if command_text else ""
        body_buffer = body
        if command_prefix:
            if body_buffer:
                if not body_buffer.startswith(command_prefix):
                    body_buffer = f"{command_prefix}\n{body_buffer}"
            else:
                body_buffer = command_prefix
        initial_content = f"{header_line}\n{body_buffer}"
        last_emit_at = time.monotonic()
        yield AgentStep(
            step_type="observation",
            content=initial_content,
            metadata={
                "tool": tool_name,
                "iteration": iteration,
                "stream_key": stream_key,
                "streaming": True,
                "command": str(command),
                "waiting_input": waiting_input,
                **({"wait_reason": wait_reason} if wait_reason else {})
            }
        )

        try:
            max_output = args.get("max_output")
            if max_output is None:
                max_output = int(get_tool_config().get("shell", {}).get("max_output", 20000))
            else:
                max_output = int(max_output)
        except (TypeError, ValueError):
            max_output = int(get_tool_config().get("shell", {}).get("max_output", 20000))
        try:
            keepalive_sec = float(get_tool_config().get("shell", {}).get("stream_keepalive_sec", 10))
        except (TypeError, ValueError):
            keepalive_sec = 10.0
        if keepalive_sec <= 0:
            keepalive_sec = 10.0

        timeout_sec: Optional[float] = None
        try:
            timeout_ms = args.get("timeout")
            timeout_ms = float(timeout_ms) if timeout_ms is not None else None
        except (TypeError, ValueError):
            timeout_ms = None
        if timeout_ms is not None and timeout_ms > 0:
            timeout_sec = timeout_ms / 1000.0
        if timeout_sec is None:
            try:
                timeout_value = args.get("timeout_sec")
                timeout_sec = float(timeout_value) if timeout_value is not None else None
            except (TypeError, ValueError):
                timeout_sec = None
        if timeout_sec is not None and timeout_sec <= 0:
            timeout_sec = None
        if timeout_sec is None:
            try:
                timeout_sec = float(get_tool_config().get("shell", {}).get("timeout_sec", 120))
            except (TypeError, ValueError):
                timeout_sec = 120.0

        cursor = None
        start_time = time.monotonic()
        timed_out = False
        error_output: Optional[str] = None
        last_header_line = header_line
        current_waiting_input = waiting_input
        current_wait_reason = wait_reason
        idle_timed_out = False
        completion_reached = False
        total_reads = 0
        total_bytes = 0
        last_chunk_at = time.monotonic()

        while True:
            if _stop_requested():
                await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
                stopped_output = self._append_stop_note(body_buffer or "")
                output_holder["output"] = stopped_output
                yield AgentStep(
                    step_type="observation",
                    content=stopped_output,
                    metadata={
                        "tool": tool_name,
                        "iteration": iteration,
                        "stream_key": stream_key,
                        "command": str(command),
                        "stopped_by_user": True
                    }
                )
                _pty_stream_log(
                    f"stopped stream_key={stream_key} pty_id={pty_id} reads={total_reads} bytes={total_bytes}"
                )
                return

            if timeout_sec is not None and timeout_sec > 0:
                if (time.monotonic() - start_time) >= timeout_sec:
                    timed_out = True
                    break

            read_args: Dict[str, Any] = {"action": "read", "pty_id": pty_id}
            if cursor is not None:
                read_args["cursor"] = cursor
            if max_output is not None:
                read_args["max_output"] = max_output
            if marker_enabled and completion_key:
                read_args["completion_key"] = completion_key

            read_start = time.monotonic()
            _pty_stream_log(
                f"read_start stream_key={stream_key} pty_id={pty_id} cursor={cursor} max_output={read_args.get('max_output')}"
            )
            try:
                if read_timeout_sec is not None:
                    read_output = await asyncio.wait_for(
                        self._execute_tool(tool, json.dumps(read_args)),
                        timeout=read_timeout_sec
                    )
                else:
                    read_output = await self._execute_tool(tool, json.dumps(read_args))
            except asyncio.TimeoutError:
                _pty_stream_log(
                    f"read_timeout stream_key={stream_key} pty_id={pty_id} timeout_sec={read_timeout_sec}"
                )
                if _stop_requested():
                    await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
                    stopped_output = self._append_stop_note(body_buffer or "")
                    output_holder["output"] = stopped_output
                    yield AgentStep(
                        step_type="observation",
                        content=stopped_output,
                        metadata={
                            "tool": tool_name,
                            "iteration": iteration,
                            "stream_key": stream_key,
                            "command": str(command),
                            "stopped_by_user": True
                        }
                    )
                    _pty_stream_log(
                        f"stopped_after_read_timeout stream_key={stream_key} pty_id={pty_id}"
                    )
                    return
                # Continue loop if not stopped; allow keepalive/logging to continue.
                continue
            finally:
                read_elapsed = time.monotonic() - read_start
                if read_elapsed >= 1.0:
                    _pty_stream_log(
                        f"read_slow stream_key={stream_key} pty_id={pty_id} elapsed={read_elapsed:.2f}s"
                    )
            _pty_stream_log(
                f"read_end stream_key={stream_key} pty_id={pty_id} bytes={len(read_output or '')}"
            )
            if _stop_requested():
                await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
                stopped_output = self._append_stop_note(body_buffer or "")
                output_holder["output"] = stopped_output
                yield AgentStep(
                    step_type="observation",
                    content=stopped_output,
                    metadata={
                        "tool": tool_name,
                        "iteration": iteration,
                        "stream_key": stream_key,
                        "command": str(command),
                        "stopped_by_user": True
                    }
                )
                _pty_stream_log(
                    f"stopped_after_read stream_key={stream_key} pty_id={pty_id} reads={total_reads} bytes={total_bytes}"
                )
                return
            read_header, chunk = self._split_shell_output(read_output)
            if read_header and not read_header.startswith("["):
                error_output = read_output
                _pty_stream_log(
                    f"read_error stream_key={stream_key} pty_id={pty_id} output={read_output[:120] if read_output else ''}"
                )
                break
            if read_header:
                header_data = self._parse_shell_header(read_header)
                last_header_line = read_header
                status = header_data.get("status")
                current_waiting_input = header_data.get("waiting_input") == "true"
                current_wait_reason = str(header_data.get("wait_reason") or "").strip() or None
                if header_data.get("cursor") is not None:
                    try:
                        cursor = int(header_data.get("cursor"))
                    except (TypeError, ValueError):
                        cursor = cursor
                if header_data.get("completion_reached") is not None:
                    completion_reached = header_data.get("completion_reached") == "true"
                reset = header_data.get("reset") == "true"
            else:
                reset = False
                status = None

            if chunk.strip() == "(no output)":
                chunk = ""

            emit_chunk = chunk
            total_reads += 1
            if emit_chunk:
                total_bytes += len(emit_chunk)

            if chunk:
                if "[idle_timeout]" in chunk:
                    idle_timed_out = True

            if emit_chunk:
                if reset:
                    body_buffer = emit_chunk
                    if command_prefix:
                        if body_buffer:
                            if not body_buffer.startswith(command_prefix):
                                body_buffer = f"{command_prefix}\n{body_buffer}"
                        else:
                            body_buffer = command_prefix
                else:
                    if command_prefix and body_buffer.startswith(command_prefix) and not body_buffer.endswith("\n") and not emit_chunk.startswith("\n"):
                        body_buffer += "\n"
                    body_buffer += emit_chunk
                yield AgentStep(
                    step_type="observation_delta",
                    content=emit_chunk,
                    metadata={
                        "tool": tool_name,
                        "iteration": iteration,
                        "stream_key": stream_key,
                        "reset": bool(reset),
                        "command": str(command),
                        "waiting_input": current_waiting_input,
                        **({"wait_reason": current_wait_reason} if current_wait_reason else {})
                    }
                )
                last_emit_at = time.monotonic()
                last_chunk_at = last_emit_at
            else:
                now = time.monotonic()
                if status != "exited" and not _stop_requested() and now - last_emit_at >= keepalive_sec:
                    yield AgentStep(
                        step_type="observation_delta",
                        content="",
                        metadata={
                            "tool": tool_name,
                            "iteration": iteration,
                            "stream_key": stream_key,
                            "command": str(command),
                            "keepalive": True,
                            "waiting_input": current_waiting_input,
                            **({"wait_reason": current_wait_reason} if current_wait_reason else {})
                        }
                    )
                    last_emit_at = now
                    _pty_stream_log(
                        f"keepalive stream_key={stream_key} pty_id={pty_id}"
                    )

                if now - last_log_at >= log_interval_sec:
                    since_chunk = now - last_chunk_at
                    _pty_stream_log(
                        f"loop stream_key={stream_key} pty_id={pty_id} status={status} "
                        f"cursor={cursor} reads={total_reads} bytes={total_bytes} "
                        f"since_last_chunk={since_chunk:.1f}s"
                    )
                    last_log_at = now

            if completion_reached:
                if not chunk:
                    break
                continue

            if status == "exited":
                if not chunk:
                    break
                continue

            if not chunk:
                await asyncio.sleep(0.05)

        if error_output:
            await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
            output_holder["output"] = error_output
            yield AgentStep(
                step_type="observation",
                content=error_output,
                metadata={
                    "tool": tool_name,
                    "iteration": iteration,
                    "stream_key": stream_key,
                    "waiting_input": current_waiting_input,
                    **({"wait_reason": current_wait_reason} if current_wait_reason else {})
                }
            )
            return

        if timed_out:
            await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
            final_body = body_buffer if body_buffer else "(no output)"
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            timeout_marker = f"[timeout elapsed_ms={elapsed_ms} timeout_sec={timeout_sec}]"
            if timeout_marker not in final_body:
                final_body = f"{final_body}\n{timeout_marker}"
            final_output = f"[exit_code=124]\n{final_body}"
            output_holder["output"] = final_output
            yield AgentStep(
                step_type="observation",
                content=final_output,
                metadata={
                    "tool": tool_name,
                    "iteration": iteration,
                    "stream_key": stream_key,
                    "command": str(command),
                    "waiting_input": current_waiting_input,
                    **({"wait_reason": current_wait_reason} if current_wait_reason else {})
                }
            )
            _pty_stream_log(
                f"timeout stream_key={stream_key} pty_id={pty_id} timeout_sec={timeout_sec}"
            )
            return

        await self._execute_tool(tool, json.dumps({"action": "close", "pty_id": pty_id}))
        final_body = body_buffer if body_buffer else "(no output)"
        if idle_timed_out:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            final_body = f"{final_body}\n[idle_timeout elapsed_ms={elapsed_ms}]"
        final_output = f"{last_header_line}\n{final_body}"
        output_holder["output"] = final_output
        yield AgentStep(
            step_type="observation",
            content=final_output,
            metadata={
                "tool": tool_name,
                "iteration": iteration,
                "stream_key": stream_key,
                "command": str(command),
                "waiting_input": current_waiting_input,
                **({"wait_reason": current_wait_reason} if current_wait_reason else {})
            }
        )
        _pty_stream_log(
            f"done stream_key={stream_key} pty_id={pty_id} status={status} reads={total_reads} bytes={total_bytes}"
        )

    async def _execute_tool_call(self, tools: List[Tool], tool_name: Optional[str], args_text: str) -> Tuple[str, str]:
        tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
        if error_msg:
            return tool_input, error_msg
        if tool is None:
            return tool_input, f"Tool not found: '{tool_name}'"
        output = await self._execute_tool(tool, tool_input)
        return tool_input, output

    def _build_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

    def _update_llm_processed(self, llm_call_id: int, payload: Dict[str, Any]) -> None:
        try:
            from database import db
            db.update_llm_call_processed(llm_call_id, payload)
        except Exception:
            pass

    def get_max_iterations(self) -> int:
        return self.max_iterations
