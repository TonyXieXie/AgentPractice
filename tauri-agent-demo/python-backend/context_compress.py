from typing import List, Dict, Any, Optional, Tuple
import os

from models import LLMConfig
from database import db


CONTEXT_SUMMARY_PROMPT = (
    "你是对话摘要助手。请将对话压缩为可供后续继续对话的简明摘要。\n"
    "- 只总结用户与助手之间的对话内容\n"
    "- 保留关键目标、已做结论、关键事实、约束、待办、代码/文件/命令\n"
    "- 不要包含系统提示词或工具调用过程\n"
    "- 输出纯摘要文本，不要添加标题或前缀"
)

CONTEXT_SUMMARY_MARKER = "[Context Summary]"
CONTEXT_COMPRESS_KEEP_RECENT_CALLS = 10
CONTEXT_COMPRESS_STEP_CALLS = 5
TRUNCATION_MARKER_START = "[TRUNCATED_START]"
TRUNCATION_MARKER_END = "[TRUNCATED_END]"


def _debug_log(message: str) -> None:
    if os.getenv("CONTEXT_COMPRESS_DEBUG"):
        print(f"[Context Compress] {message}")


def _estimate_tokens_for_text(text: str) -> int:
    if not text:
        return 0
    ascii_count = 0
    non_ascii = 0
    for ch in text:
        if ord(ch) <= 0x7F:
            ascii_count += 1
        else:
            non_ascii += 1
    return (ascii_count + 3) // 4 + non_ascii


def _estimate_tokens_for_messages(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 4
        total += _estimate_tokens_for_text(str(msg.get("content") or ""))
    return total


def _format_dialogue_for_summary(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            prefix = "User"
        elif role == "assistant":
            prefix = "Assistant"
        else:
            continue
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


def _truncate_text_middle(text: str, cfg: Optional[Dict[str, Any]]) -> str:
    if text is None:
        return ""
    if not cfg or not cfg.get("enabled", True):
        return str(text)
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
        f"{head_text}\n{TRUNCATION_MARKER_START}({omitted} chars omitted)\n"
        f"{TRUNCATION_MARKER_END}\n{tail_text}"
    )


def _build_trunc_cfg(context_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "enabled": bool(context_cfg.get("truncate_long_data", True)),
        "threshold": int(context_cfg.get("long_data_threshold", 4000) or 4000),
        "head_chars": int(context_cfg.get("long_data_head_chars", 1200) or 1200),
        "tail_chars": int(context_cfg.get("long_data_tail_chars", 800) or 800)
    }


def _build_context_summary_request(summary: str, dialogue_text: str) -> List[Dict[str, str]]:
    parts = []
    if summary:
        parts.append(f"已有摘要：\n{summary}")
    if dialogue_text:
        parts.append(f"新增对话：\n{dialogue_text}")
    combined = "\n\n".join(parts).strip()
    if not combined:
        combined = "请生成摘要。"
    user_prompt = f"{combined}\n\n请输出更新后的摘要，只输出摘要正文。"
    return [
        {"role": "system", "content": CONTEXT_SUMMARY_PROMPT},
        {"role": "user", "content": user_prompt}
    ]


async def _run_context_summary(
    llm_client: Any,
    summary: str,
    dialogue_messages: List[Dict[str, Any]]
) -> Optional[str]:
    dialogue_text = _format_dialogue_for_summary(dialogue_messages)
    if not dialogue_text and not summary:
        return None
    request_messages = _build_context_summary_request(summary, dialogue_text)
    try:
        result = await llm_client.chat(request_messages)
    except Exception as exc:
        print(f"[Context Compress] LLM request failed: {exc}")
        return None
    content = str(result.get("content") or "").strip()
    return content or None


async def summarize_dialogue(
    llm_client: Any,
    summary: str,
    dialogue_messages: List[Dict[str, Any]]
) -> Optional[str]:
    return await _run_context_summary(llm_client, summary, dialogue_messages)


def build_history_for_llm(
    session_id: str,
    after_message_id: Optional[int],
    current_user_message_id: Optional[int],
    summary: str,
    code_map: Optional[str],
    trunc_cfg: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    messages = db.get_dialogue_messages_after(session_id, after_message_id)
    filtered = []
    for msg in messages:
        if current_user_message_id and msg.get("id") == current_user_message_id:
            continue
        if msg.get("role") == "assistant":
            content_text = str(msg.get("content") or "")
            if not content_text.strip():
                continue
        filtered.append(msg)
    assistant_ids = [msg.get("id") for msg in filtered if msg.get("role") == "assistant"]
    steps_by_message: Dict[int, List[Dict[str, Any]]] = {}
    if assistant_ids:
        steps = db.get_session_agent_steps_for_messages(session_id, assistant_ids)
        for step in steps:
            message_id = step.get("message_id")
            if not message_id:
                continue
            steps_by_message.setdefault(message_id, []).append(step)

    history: List[Dict[str, Any]] = []
    tool_call_counter = 0
    for msg in filtered:
        if msg.get("role") == "assistant":
            msg_id = msg.get("id")
            steps = steps_by_message.get(msg_id, [])
            pending_calls: List[Dict[str, str]] = []

            def _next_call_id(sequence: Optional[int], tool_name: str) -> str:
                nonlocal tool_call_counter
                tool_call_counter += 1
                seq = sequence if isinstance(sequence, int) else tool_call_counter
                return f"hist_call_{msg_id}_{seq}_{tool_call_counter}"

            for step in steps:
                step_type = step.get("step_type")
                metadata = step.get("metadata") or {}
                tool_name = metadata.get("tool")
                if not tool_name:
                    continue
                if step_type == "observation" and metadata.get("context_compress"):
                    continue
                if step_type == "action":
                    tool_input = metadata.get("input")
                    if tool_input is None:
                        tool_input = ""
                    tool_input = _truncate_text_middle(tool_input, trunc_cfg)
                    call_id = _next_call_id(step.get("sequence"), str(tool_name))
                    pending_calls.append({"tool": str(tool_name), "id": call_id})
                    history.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": str(tool_name),
                                    "arguments": str(tool_input)
                                }
                            }
                        ]
                    })
                elif step_type == "observation":
                    tool_output = _truncate_text_middle(step.get("content") or "", trunc_cfg)
                    call_id = None
                    for idx, item in enumerate(pending_calls):
                        if item.get("tool") == str(tool_name):
                            call_id = item.get("id")
                            pending_calls.pop(idx)
                            break
                    if call_id is None:
                        call_id = _next_call_id(step.get("sequence"), str(tool_name))
                        history.append({
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": str(tool_name),
                                        "arguments": ""
                                    }
                                }
                            ]
                        })
                    history.append({
                        "role": "tool",
                        "content": tool_output,
                        "tool_call_id": call_id,
                        "name": str(tool_name)
                    })

        history.append({"role": msg.get("role"), "content": msg.get("content")})

    if summary:
        history.insert(0, {"role": "assistant", "content": f"{CONTEXT_SUMMARY_MARKER}\n{summary}"})
    if code_map:
        insert_index = 1 if summary else 0
        history.insert(insert_index, {"role": "assistant", "content": code_map})
    return history


async def maybe_compress_context(
    session_id: str,
    config: LLMConfig,
    app_config: Dict[str, Any],
    llm_client: Any,
    current_summary: str,
    last_compressed_call_id: Optional[int],
    current_user_message_id: Optional[int],
    current_user_text: str,
    current_total_tokens: Optional[int] = None
) -> Tuple[str, Optional[int], Optional[int], bool]:
    if not session_id or not current_user_message_id:
        _debug_log("skip: missing session_id or current_user_message_id")
        return current_summary, last_compressed_call_id, None, False

    context_cfg = app_config.get("context", {}) if isinstance(app_config, dict) else {}
    if not context_cfg.get("compression_enabled"):
        _debug_log("skip: compression disabled")
        return current_summary, last_compressed_call_id, None, False

    try:
        start_pct = int(context_cfg.get("compress_start_pct", 75))
    except (TypeError, ValueError):
        start_pct = 75
    try:
        target_pct = int(context_cfg.get("compress_target_pct", 55))
    except (TypeError, ValueError):
        target_pct = 55
    try:
        min_keep_messages = int(context_cfg.get("min_keep_messages", 1))
    except (TypeError, ValueError):
        min_keep_messages = 1
    try:
        keep_recent_calls = int(context_cfg.get("keep_recent_calls", CONTEXT_COMPRESS_KEEP_RECENT_CALLS))
    except (TypeError, ValueError):
        keep_recent_calls = CONTEXT_COMPRESS_KEEP_RECENT_CALLS
    try:
        step_calls = int(context_cfg.get("step_calls", CONTEXT_COMPRESS_STEP_CALLS))
    except (TypeError, ValueError):
        step_calls = CONTEXT_COMPRESS_STEP_CALLS

    if keep_recent_calls < 0:
        keep_recent_calls = 0
    if step_calls < 1:
        step_calls = 1

    max_tokens = getattr(config, "max_context_tokens", 0) or 0
    if max_tokens <= 0:
        _debug_log("skip: max_context_tokens <= 0")
        return current_summary, last_compressed_call_id, None, False

    trunc_cfg = _build_trunc_cfg(context_cfg)

    summary = current_summary or ""
    last_call_id = int(last_compressed_call_id or 0)
    last_message_id = db.get_max_message_id_for_llm_call(session_id, last_call_id) if last_call_id else None
    _debug_log(
        f"start: max_tokens={max_tokens} start_pct={start_pct} target_pct={target_pct} "
        f"keep_recent_calls={keep_recent_calls} step_calls={step_calls} last_call_id={last_call_id} "
        f"last_message_id={last_message_id}"
    )

    def build_uncompressed_messages(after_id: Optional[int]) -> List[Dict[str, Any]]:
        messages = db.get_dialogue_messages_after(session_id, after_id)
        filtered = []
        for msg in messages:
            if msg.get("id") == current_user_message_id:
                continue
            if msg.get("role") == "assistant":
                content_text = str(msg.get("content") or "")
                if not content_text.strip():
                    continue
            filtered.append(msg)
        return filtered

    history_for_llm = build_history_for_llm(
        session_id,
        last_message_id,
        current_user_message_id,
        summary,
        None,
        trunc_cfg
    )
    if current_total_tokens is not None:
        initial_tokens = int(current_total_tokens)
        _debug_log(f"using current_total_tokens={initial_tokens}")
    else:
        initial_tokens = _estimate_tokens_for_messages(history_for_llm)
        if current_user_text:
            initial_tokens += _estimate_tokens_for_text(current_user_text)
    if initial_tokens < (start_pct / 100.0) * max_tokens:
        _debug_log(f"skip: initial_tokens={initial_tokens} below threshold")
        return summary, last_compressed_call_id, last_message_id, False

    keep_window = keep_recent_calls
    did_compress = False

    while True:
        calls_after = db.get_llm_call_metas_after(session_id, last_call_id)
        if len(calls_after) <= keep_window:
            _debug_log(f"stop: calls_after={len(calls_after)} <= keep_window={keep_window}")
            break

        protected_calls = calls_after[-keep_window:] if keep_window > 0 else []
        protected_message_ids = {call["message_id"] for call in protected_calls if call.get("message_id")}
        compressible_calls = calls_after[:-keep_window] if keep_window > 0 else calls_after

        boundary_call = None
        for call in reversed(compressible_calls):
            message_id = call.get("message_id")
            if not message_id:
                continue
            if protected_message_ids and message_id in protected_message_ids:
                continue
            boundary_call = call
            break
        if not boundary_call:
            _debug_log(
                f"no boundary: keep_window={keep_window} protected_ids={len(protected_message_ids)}"
            )
            if keep_window <= 0:
                break
            if keep_window > 1:
                keep_window = max(1, keep_window - step_calls)
            else:
                keep_window = 0
            continue

        boundary_call_id = int(boundary_call["id"])
        boundary_message_id = db.get_max_message_id_for_llm_call(session_id, boundary_call_id)
        if not boundary_message_id:
            _debug_log(f"stop: no boundary_message_id for call {boundary_call_id}")
            break

        messages_between = db.get_dialogue_messages_between(
            session_id,
            (last_message_id or 0) + 1,
            boundary_message_id
        )
        if not messages_between:
            _debug_log("stop: no messages_between to compress")
            break

        compressible_assistant_ids = {
            call["message_id"]
            for call in compressible_calls
            if call.get("message_id") and call["id"] <= boundary_call_id and call["message_id"] not in protected_message_ids
        }
        if not compressible_assistant_ids:
            _debug_log("stop: no compressible_assistant_ids")
            break

        id_to_index = {msg["id"]: idx for idx, msg in enumerate(messages_between)}
        compressible_message_ids = set()
        for assistant_id in compressible_assistant_ids:
            idx = id_to_index.get(assistant_id)
            if idx is None:
                continue
            compressible_message_ids.add(assistant_id)
            for back_idx in range(idx - 1, -1, -1):
                if messages_between[back_idx]["role"] == "user":
                    compressible_message_ids.add(messages_between[back_idx]["id"])
                    break

        compressible_message_ids.discard(current_user_message_id)
        if not compressible_message_ids:
            _debug_log("stop: no compressible_message_ids")
            break

        compress_messages = [
            msg for msg in messages_between if msg["id"] in compressible_message_ids
        ]
        if not compress_messages:
            _debug_log("stop: compress_messages empty")
            break

        uncompressed_after = build_uncompressed_messages(boundary_message_id)
        if len(uncompressed_after) < min_keep_messages:
            _debug_log(
                f"stop: uncompressed_after={len(uncompressed_after)} < min_keep_messages={min_keep_messages}"
            )
            break

        new_summary = await _run_context_summary(llm_client, summary, compress_messages)
        if not new_summary:
            _debug_log("stop: summary generation returned empty")
            break

        summary = new_summary
        last_call_id = boundary_call_id
        last_message_id = boundary_message_id
        did_compress = True

        history_for_llm = build_history_for_llm(
            session_id,
            last_message_id,
            current_user_message_id,
            summary,
            None,
            trunc_cfg
        )
        token_count = _estimate_tokens_for_messages(history_for_llm)
        if current_user_text:
            token_count += _estimate_tokens_for_text(current_user_text)
        if token_count <= (target_pct / 100.0) * max_tokens:
            break

        if keep_window <= 0:
            break
        keep_window = max(0, keep_window - step_calls)

    return summary, last_call_id if did_compress else last_compressed_call_id, last_message_id, did_compress
