from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.center import HTTP_INGRESS_SENDER_ID
from agents.message import AgentMessage
from observation.facts import PrivateExecutionEvent, SharedFact


CONTROL_OVERRIDE_KEYS = {
    "strategy",
    "llm_config",
    "tool_name",
    "tool_arguments",
    "work_path",
    "system_prompt",
    "session_id",
    "stream",
}


def _payload(message: AgentMessage) -> Dict[str, Any]:
    return deepcopy(message.payload) if isinstance(message.payload, dict) else {}


def get_request_overrides(message: AgentMessage) -> Dict[str, Any]:
    payload = _payload(message)
    value = payload.get("request_overrides")
    return deepcopy(value) if isinstance(value, dict) else {}


def build_llm_request_overrides(message: AgentMessage) -> Dict[str, Any]:
    filtered: Dict[str, Any] = {}
    for key, value in get_request_overrides(message).items():
        if key in CONTROL_OVERRIDE_KEYS:
            continue
        filtered[key] = deepcopy(value)
    return filtered


def get_execution_metadata(message: AgentMessage) -> Dict[str, Any]:
    metadata = deepcopy(message.metadata) if isinstance(message.metadata, dict) else {}
    payload = _payload(message)
    payload_metadata = payload.get("metadata")
    if isinstance(payload_metadata, dict):
        metadata.update(deepcopy(payload_metadata))
    return metadata


def get_strategy_name(message: AgentMessage, *, default: str = "react") -> str:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    return str(
        payload.get("strategy")
        or request_overrides.get("strategy")
        or default
        or "react"
    ).lower()


def get_tool_name(message: AgentMessage) -> Optional[str]:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    value = payload.get("tool_name") or request_overrides.get("tool_name")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_tool_arguments(message: AgentMessage) -> Dict[str, Any]:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    value = payload.get("tool_arguments")
    if isinstance(value, dict):
        return deepcopy(value)
    override_value = request_overrides.get("tool_arguments")
    if isinstance(override_value, dict):
        return deepcopy(override_value)
    return {}


def get_llm_config(message: AgentMessage) -> Optional[Dict[str, Any]]:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    value = payload.get("llm_config")
    if isinstance(value, dict):
        return deepcopy(value)
    override_value = request_overrides.get("llm_config")
    if isinstance(override_value, dict):
        return deepcopy(override_value)
    return None


def get_system_prompt(message: AgentMessage) -> Optional[str]:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    value = payload.get("system_prompt") or request_overrides.get("system_prompt")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_work_path(message: AgentMessage) -> Optional[str]:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    value = payload.get("work_path") or request_overrides.get("work_path")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_session_id(message: AgentMessage) -> Optional[str]:
    request_overrides = get_request_overrides(message)
    payload = _payload(message)
    value = (
        message.session_id
        or payload.get("session_id")
        or request_overrides.get("session_id")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_user_input(message: AgentMessage) -> str:
    payload = _payload(message)
    return str(payload.get("content", "") or "")


def render_current_message(message: AgentMessage) -> str:
    return render_agent_message_text(message)


def render_agent_message_text(message: AgentMessage) -> str:
    payload = _payload(message)
    if message.sender_id == HTTP_INGRESS_SENDER_ID:
        return render_external_user_text(payload)
    speaker = message.sender_id or "Unknown"
    return render_speaker_line(speaker, render_payload_text(payload, fallback_topic=message.topic))


def render_external_user_text(payload: Optional[Dict[str, Any]]) -> str:
    normalized_payload = deepcopy(payload) if isinstance(payload, dict) else {}
    return (
        _coerce_text(normalized_payload.get("content"))
        or _coerce_text(normalized_payload.get("text"))
        or _coerce_text(normalized_payload.get("reply"))
        or _safe_json(normalized_payload)
    ).strip()


def render_shared_fact_entry(fact: SharedFact) -> Optional[Dict[str, Any]]:
    if fact.sender_id == HTTP_INGRESS_SENDER_ID:
        content = render_external_user_text(fact.payload)
        if not content:
            return None
        return {"role": "user", "content": content}
    content = render_speaker_line(
        fact.sender_id or "Unknown",
        render_payload_text(fact.payload, fallback_topic=fact.topic),
    )
    if not content.strip():
        return None
    return {"role": "assistant", "content": content}


def render_private_event_entry(
    event: PrivateExecutionEvent,
    *,
    current_agent_id: str,
) -> Optional[Dict[str, Any]]:
    payload = event.payload
    if event.kind == "tool_call":
        body = _render_tool_call_text(payload)
    elif event.kind == "tool_result":
        body = _render_tool_result_text(payload)
    elif event.kind in {"reasoning_note", "reasoning_summary", "private_summary", "execution_error"}:
        body = (
            _coerce_text(payload.get("content"))
            or _coerce_text(payload.get("summary_text"))
            or _coerce_text(payload.get("error"))
            or _coerce_text(payload.get("status"))
            or _safe_json(payload)
        )
    else:
        body = (
            _coerce_text(payload.get("content"))
            or _coerce_text(payload.get("text"))
            or _coerce_text(payload.get("error"))
            or _safe_json(payload)
        )
    body = str(body or "").strip()
    if not body:
        return None
    return {"role": "assistant", "content": render_speaker_line(current_agent_id, body)}


def render_payload_text(
    payload: Optional[Dict[str, Any]],
    *,
    fallback_topic: Optional[str] = None,
) -> str:
    normalized_payload = deepcopy(payload) if isinstance(payload, dict) else {}
    handoff_text = _render_handoff_payload_text(normalized_payload)
    if handoff_text:
        return handoff_text
    return (
        _coerce_text(normalized_payload.get("content"))
        or _coerce_text(normalized_payload.get("text"))
        or _coerce_text(normalized_payload.get("reply"))
        or _coerce_text(normalized_payload.get("result"))
        or _coerce_text(normalized_payload.get("error"))
        or _coerce_text(normalized_payload.get("summary_text"))
        or _safe_json(normalized_payload)
        or str(fallback_topic or "").strip()
    ).strip()


def _render_handoff_payload_text(payload: Dict[str, Any]) -> str:
    if not (
        payload.get("handoff_to_profile")
        or payload.get("handoff_from_profile")
        or payload.get("handoff_reason")
        or payload.get("handoff_context")
    ):
        return ""
    content = _coerce_text(payload.get("content")) or ""
    lines = [content.strip()] if content.strip() else []
    reason = _coerce_text(payload.get("handoff_reason")) or ""
    if reason.strip():
        lines.append(f"Handoff reason: {reason.strip()}")
    context = payload.get("handoff_context")
    if isinstance(context, dict) and context:
        lines.append(f"Handoff context: {_safe_json(context)}")
    return "\n".join(line for line in lines if line).strip()


def render_speaker_line(speaker: str, body: str) -> str:
    normalized_speaker = str(speaker or "").strip() or "Unknown"
    normalized_body = str(body or "").strip()
    if not normalized_body:
        return ""
    return f"[{normalized_speaker}] {normalized_body}"


def get_history(message: AgentMessage) -> List[Dict[str, Any]]:
    payload = _payload(message)
    value = payload.get("history")
    if not isinstance(value, list):
        return []
    history: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = item.get("content")
        if not role or content is None:
            continue
        history.append({"role": role, "content": deepcopy(content)})
    return history


def stream_enabled(message: AgentMessage) -> bool:
    return bool(get_request_overrides(message).get("stream", True))


def _render_tool_call_text(payload: Dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or "tool").strip()
    arguments = payload.get("arguments")
    rendered_args = _coerce_text(arguments) or _safe_json(arguments)
    if rendered_args:
        return f"我调用了工具 {tool_name}，参数是：{rendered_args}"
    return f"我调用了工具 {tool_name}。"


def _render_tool_result_text(payload: Dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or "tool").strip()
    ok = payload.get("ok")
    if ok is False:
        error_text = _coerce_text(payload.get("error")) or "工具执行失败。"
        return f"工具 {tool_name} 执行失败：{error_text}"
    output_text = _coerce_text(payload.get("output"))
    if output_text:
        return f"工具 {tool_name} 返回：{output_text}"
    return f"工具 {tool_name} 已执行完成。"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in (
            "content",
            "reply",
            "result",
            "text",
            "error",
            "value",
            "output",
            "summary_text",
        ):
            text = _coerce_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    return _safe_json(value).strip()
