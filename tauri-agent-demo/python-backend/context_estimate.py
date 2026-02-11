from typing import List, Dict, Any, Optional
import json


def estimate_tokens_for_text(text: str) -> int:
    if not text:
        return 0
    ascii_count = 0
    non_ascii = 0
    for ch in str(text):
        if ord(ch) <= 0x7F:
            ascii_count += 1
        else:
            non_ascii += 1
    return (ascii_count + 3) // 4 + non_ascii


def estimate_tokens_for_messages(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 4
        total += estimate_tokens_for_text(str(msg.get("content") or ""))
    return total


def estimate_tokens_by_role(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    system_tokens = 0
    history_tokens = 0
    for msg in messages:
        tokens = 4 + estimate_tokens_for_text(str(msg.get("content") or ""))
        role = str(msg.get("role") or "")
        if role in ("system", "developer"):
            system_tokens += tokens
        else:
            history_tokens += tokens
    return {"system": system_tokens, "history": history_tokens}


def estimate_tool_tokens(tools_payload: Optional[Any]) -> int:
    if not tools_payload:
        return 0
    try:
        return estimate_tokens_for_text(json.dumps(tools_payload))
    except Exception:
        return 0


def build_context_estimate(
    messages: List[Dict[str, Any]],
    tools_payload: Optional[Any] = None,
    max_tokens: Optional[int] = None,
    updated_at: Optional[str] = None
) -> Dict[str, Any]:
    role_tokens = estimate_tokens_by_role(messages)
    tools_tokens = estimate_tool_tokens(tools_payload)
    system_tokens = role_tokens.get("system", 0)
    history_tokens = role_tokens.get("history", 0)
    other_tokens = 0
    total = system_tokens + history_tokens + tools_tokens + other_tokens
    result: Dict[str, Any] = {
        "total": total,
        "system": system_tokens,
        "history": history_tokens,
        "tools": tools_tokens,
        "other": other_tokens
    }
    if max_tokens is not None:
        result["max_tokens"] = max_tokens
    if updated_at:
        result["updated_at"] = updated_at
    return result
