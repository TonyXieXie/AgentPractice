from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.message import AgentMessage


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


def get_strategy_name(message: AgentMessage, *, default: str = "simple") -> str:
    payload = _payload(message)
    request_overrides = get_request_overrides(message)
    return str(
        payload.get("strategy")
        or request_overrides.get("strategy")
        or default
        or "simple"
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
    value = message.session_id or payload.get("session_id") or request_overrides.get("session_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_user_input(message: AgentMessage) -> str:
    payload = _payload(message)
    return str(payload.get("content", "") or "")


def render_current_message(message: AgentMessage) -> str:
    return render_message_envelope(
        message_type=message.message_type,
        rpc_phase=message.rpc_phase,
        sender_id=message.sender_id,
        topic=message.topic,
        payload=_payload(message),
        ok=message.ok,
        prefer_plain_task_run=True,
    )


def render_message_center_entry(
    *,
    message_type: str,
    rpc_phase: Optional[str],
    sender_id: Optional[str],
    topic: Optional[str],
    payload: Optional[Dict[str, Any]],
    ok: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    content = render_message_envelope(
        message_type=message_type,
        rpc_phase=rpc_phase,
        sender_id=sender_id,
        topic=topic,
        payload=payload,
        ok=ok,
        prefer_plain_task_run=False,
    )
    if not content.strip():
        return None
    role = "user" if message_type == "rpc" and rpc_phase == "request" else "assistant"
    return {"role": role, "content": content}


def render_message_envelope(
    *,
    message_type: str,
    rpc_phase: Optional[str],
    sender_id: Optional[str],
    topic: Optional[str],
    payload: Optional[Dict[str, Any]],
    ok: Optional[bool] = None,
    prefer_plain_task_run: bool = False,
) -> str:
    normalized_payload = deepcopy(payload) if isinstance(payload, dict) else {}
    normalized_message_type = str(message_type or "").strip()
    normalized_rpc_phase = str(rpc_phase or "").strip()
    normalized_topic = str(topic or "").strip()
    normalized_sender = str(sender_id or "").strip() or "unknown"

    if normalized_message_type == "rpc" and normalized_rpc_phase == "request":
        body = (
            _coerce_text(normalized_payload.get("content"))
            or _coerce_text(normalized_payload.get("reply"))
            or _coerce_text(normalized_payload.get("result"))
            or _safe_json(normalized_payload)
        )
        if prefer_plain_task_run and normalized_topic == "task.run":
            return body.strip()
        header = f"[RPC request] from {normalized_sender} topic={normalized_topic or 'unknown'}"
        return f"{header}\n{body}".strip()

    if normalized_message_type == "rpc" and normalized_rpc_phase == "response":
        body = (
            _coerce_text(normalized_payload.get("reply"))
            or _coerce_text(normalized_payload.get("result"))
            or _coerce_text(normalized_payload.get("error"))
            or _safe_json(normalized_payload)
        )
        header = (
            f"[RPC response] from {normalized_sender} topic={normalized_topic or 'unknown'} ok={ok}"
        )
        return f"{header}\n{body}".strip()

    if normalized_message_type == "event":
        body = (
            _coerce_text(normalized_payload.get("text"))
            or _coerce_text(normalized_payload.get("content"))
            or _safe_json(normalized_payload)
        )
        header = f"[Event] from {normalized_sender} topic={normalized_topic or 'unknown'}"
        return f"{header}\n{body}".strip()

    return (
        _coerce_text(normalized_payload.get("content"))
        or _coerce_text(normalized_payload.get("reply"))
        or _coerce_text(normalized_payload.get("result"))
        or _safe_json(normalized_payload)
    ).strip()


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
        for key in ("content", "reply", "result", "text", "error", "value", "output"):
            text = _coerce_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        parts = [_coerce_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    return _safe_json(value).strip()
