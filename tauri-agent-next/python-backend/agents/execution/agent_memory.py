from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional

from app_config import get_app_config
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.conversation_repository import ConversationEventRecord, ConversationRepository
from repositories.message_center_repository import MessageCenterEventRecord, MessageCenterRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository


PRIVATE_CONTEXT_SUMMARY_PROMPT = (
    "你是执行过程摘要助手。请将该 Agent 的私有执行过程压缩为可供后续继续工作的简明摘要。\n"
    "- 只总结工具调用结果、关键中间结论、产生/修改的文件路径、命令、错误与修复尝试\n"
    "- 不要复述 shared 对话原文（shared 由 Message Center 负责保存）\n"
    "- 如果有产物，只记录路径/索引，不要粘贴大段内容\n"
    "- 输出纯摘要文本，不要添加标题或前缀"
)

PRIVATE_CONTEXT_SUMMARY_MARKER = "[Private Summary]"
TRUNCATION_MARKER_START = "[TRUNCATED_START]"
TRUNCATION_MARKER_END = "[TRUNCATED_END]"

DEFAULT_TOOL_POLICY_TEXT = (
    "工具定义通过 API tools 字段提供；如需使用工具，请输出工具调用参数并等待工具结果。"
)


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
        content = msg.get("content")
        total += _estimate_tokens_for_text(_coerce_text(content))
    return total


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_coerce_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "delta", "value", "output", "error", "reply"):
            if key in value and value[key] is not None:
                return _coerce_text(value[key])
    return str(value)


def _truncate_text_middle(text: str, cfg: Dict[str, Any]) -> tuple[str, bool]:
    if not cfg.get("enabled", True):
        return text, False
    threshold = int(cfg.get("threshold_chars", 4000) or 4000)
    if threshold <= 0 or len(text) <= threshold:
        return text, False
    head = max(0, int(cfg.get("head_chars", 800) or 0))
    tail = max(0, int(cfg.get("tail_chars", 800) or 0))
    if head + tail >= len(text):
        return text, False
    omitted = len(text) - head - tail
    head_text = text[:head] if head > 0 else ""
    tail_text = text[-tail:] if tail > 0 else ""
    truncated = (
        f"{head_text}"
        f"{TRUNCATION_MARKER_START}({omitted} chars omitted){TRUNCATION_MARKER_END}"
        f"{tail_text}"
    )
    return truncated, True


class AgentMemory:
    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_center_repository: MessageCenterRepository,
        conversation_repository: ConversationRepository,
        agent_prompt_state_repository: AgentPromptStateRepository,
        prompt_trace_repository: PromptTraceRepository,
    ) -> None:
        self.session_repository = session_repository
        self.message_center_repository = message_center_repository
        self.conversation_repository = conversation_repository
        self.agent_prompt_state_repository = agent_prompt_state_repository
        self.prompt_trace_repository = prompt_trace_repository

    async def build_messages(
        self,
        request,
        *,
        llm_client: Any,
        default_system_prompt: str,
        tool_policy_text: str = DEFAULT_TOOL_POLICY_TEXT,
        max_history_events: int = 20,
        budget_cfg: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        cfg = self._resolve_cfg(budget_cfg)
        system_role = self._system_role_for_client(llm_client)
        system_prompt = await self._build_system_prompt(
            request,
            default_system_prompt=default_system_prompt,
            tool_policy_text=tool_policy_text,
        )

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": system_role, "content": system_prompt})

        session_id = getattr(request, "session_id", None)
        agent_id = str(getattr(request, "agent_id", "") or "")
        actions: Dict[str, Any] = {"actions": [], "cfg": deepcopy(cfg)}

        if session_id and agent_id:
            messages = await self._append_shared_and_private_history(
                messages,
                request=request,
                llm_client=llm_client,
                session_id=str(session_id),
                agent_id=agent_id,
                max_history_events=max_history_events,
                cfg=cfg,
                actions=actions,
            )
        else:
            messages.extend(self._render_inline_history(getattr(request, "history", []), max_history_events))

        messages.append({"role": "user", "content": getattr(request, "user_input", "")})

        messages = await self.ensure_budget_for_messages(
            messages,
            llm_client=llm_client,
            session_id=str(session_id) if session_id else None,
            agent_id=agent_id or None,
            run_id=getattr(request, "run_id", None),
            phase="build_messages",
            actions=actions,
        )
        return messages

    async def ensure_budget_for_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        llm_client: Any,
        session_id: Optional[str],
        agent_id: Optional[str] = None,
        run_id: Optional[str],
        phase: str,
        actions: Optional[Dict[str, Any]] = None,
        iteration: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        cfg = self._resolve_cfg(None)
        if actions is None:
            actions = {"actions": [], "cfg": deepcopy(cfg)}
        actions.setdefault("phase", phase)
        if iteration is not None:
            actions.setdefault("iteration", iteration)

        max_context_tokens, prompt_budget = self._resolve_prompt_budget(llm_client, cfg)
        trunc_cfg = cfg.get("truncation", {})

        for idx, msg in enumerate(messages):
            content = _coerce_text(msg.get("content"))
            truncated, did = _truncate_text_middle(content, trunc_cfg)
            if did:
                msg["content"] = truncated
                actions["actions"].append(
                    {
                        "type": "truncate",
                        "message_index": idx,
                        "original_chars": len(content),
                        "threshold_chars": int(trunc_cfg.get("threshold_chars", 4000) or 4000),
                    }
                )

        estimated = _estimate_tokens_for_messages(messages)
        min_keep = max(4, int(cfg.get("min_keep_messages", 8) or 8))
        while estimated > prompt_budget and len(messages) > min_keep:
            drop_index = self._find_drop_index(messages)
            if drop_index is None:
                break
            dropped = messages.pop(drop_index)
            actions["actions"].append(
                {
                    "type": "drop_message",
                    "message_index": drop_index,
                    "role": dropped.get("role"),
                    "reason": "over_budget",
                }
            )
            estimated = _estimate_tokens_for_messages(messages)

        if session_id and cfg.get("trace", {}).get("enabled", True):
            try:
                await self.prompt_trace_repository.append(
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    llm_model=str(getattr(getattr(llm_client, "config", None), "model", None) or ""),
                    max_context_tokens=max_context_tokens,
                    prompt_budget=prompt_budget,
                    estimated_prompt_tokens=estimated,
                    rendered_message_count=len(messages),
                    actions=actions,
                )
            except Exception:
                pass

        return messages

    async def _append_shared_and_private_history(
        self,
        messages: List[Dict[str, Any]],
        *,
        request,
        llm_client: Any,
        session_id: str,
        agent_id: str,
        max_history_events: int,
        cfg: Dict[str, Any],
        actions: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        exclude_message_id = str(getattr(request, "message_id", "") or "").strip() or None
        max_shared = int(cfg.get("context", {}).get("max_shared_messages", 2000) or 2000)
        shared_records = await self.message_center_repository.list_latest_visible(
            session_id,
            agent_id,
            limit=max_shared,
            exclude_message_id=exclude_message_id,
        )
        shared_rendered = self._render_message_center_history(shared_records)
        if max_history_events > 0 and len(shared_rendered) > max_history_events:
            shared_rendered = shared_rendered[-max_history_events:]
            actions["actions"].append(
                {
                    "type": "trim_shared_history",
                    "kept": max_history_events,
                    "dropped": max(0, len(self._render_message_center_history(shared_records)) - max_history_events),
                }
            )
        messages.extend(shared_rendered)

        state = await self.agent_prompt_state_repository.get_or_create(session_id, agent_id)
        cursor = int(state.summarized_until_event_id)
        private_events = await self.conversation_repository.list_events_after(
            session_id,
            agent_id=agent_id,
            after_id=cursor,
            limit=int(cfg.get("max_unsummarized_events", 2000) or 2000),
        )

        if cfg.get("context", {}).get("compression_enabled", True) and llm_client is not None:
            private_events, state = await self._maybe_rollup_private_summary(
                session_id=session_id,
                agent_id=agent_id,
                llm_client=llm_client,
                current_summary=state.summary_text,
                summarized_until_event_id=cursor,
                events=private_events,
                cfg=cfg,
                actions=actions,
            )

        summary_text = (state.summary_text or "").strip()
        if summary_text:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"{PRIVATE_CONTEXT_SUMMARY_MARKER}\n{summary_text}",
                }
            )

        private_rendered = self._render_private_events(private_events)
        messages.extend(private_rendered)

        inline_history = getattr(request, "history", None)
        if inline_history:
            messages.extend(self._render_inline_history(inline_history, max_history_events))
            actions["actions"].append({"type": "inline_history_appended", "count": len(inline_history)})

        return messages

    async def _maybe_rollup_private_summary(
        self,
        *,
        session_id: str,
        agent_id: str,
        llm_client: Any,
        current_summary: str,
        summarized_until_event_id: int,
        events: List[ConversationEventRecord],
        cfg: Dict[str, Any],
        actions: Dict[str, Any],
    ) -> tuple[List[ConversationEventRecord], Any]:
        keep_recent = int(cfg.get("context", {}).get("keep_recent_events", 20) or 20)
        if keep_recent < 1:
            keep_recent = 1
        max_context_tokens, prompt_budget = self._resolve_prompt_budget(llm_client, cfg)
        start_pct = int(cfg.get("context", {}).get("compress_start_pct", 75) or 75)
        target_pct = int(cfg.get("context", {}).get("compress_target_pct", 55) or 55)
        start_threshold = min(int((max_context_tokens * start_pct) / 100.0), prompt_budget)
        target_threshold = min(int((max_context_tokens * target_pct) / 100.0), prompt_budget)

        def build_preview_messages(summary_text: str, preview_events: List[ConversationEventRecord]) -> List[Dict[str, Any]]:
            preview: List[Dict[str, Any]] = []
            if summary_text.strip():
                preview.append({"role": "assistant", "content": f"{PRIVATE_CONTEXT_SUMMARY_MARKER}\n{summary_text.strip()}"})
            preview.extend(self._render_private_events(preview_events))
            return preview

        preview_messages = build_preview_messages(current_summary or "", events)
        estimated = _estimate_tokens_for_messages(preview_messages)
        if estimated <= start_threshold and len(events) <= keep_recent:
            return events, type("State", (), {"summary_text": current_summary, "summarized_until_event_id": summarized_until_event_id})()

        summary_text = current_summary or ""
        cursor = summarized_until_event_id
        remaining = list(events)
        safety_loops = 0
        while safety_loops < 8 and (estimated > target_threshold or len(remaining) > keep_recent):
            safety_loops += 1
            if len(remaining) <= keep_recent:
                break
            boundary_index = max(1, len(remaining) - keep_recent)
            to_summarize = remaining[:boundary_index]
            boundary_event_id = int(to_summarize[-1].id)
            new_summary = await self._run_private_summary_call(
                llm_client,
                previous_summary=summary_text,
                events=to_summarize,
                cfg=cfg,
            )
            if not new_summary.strip():
                break

            actions["actions"].append(
                {
                    "type": "summarize_private",
                    "from_event_id": int(to_summarize[0].id),
                    "to_event_id": boundary_event_id,
                    "events": len(to_summarize),
                }
            )
            summary_text = new_summary.strip()
            cursor = boundary_event_id
            await self.agent_prompt_state_repository.update(
                session_id,
                agent_id,
                summary_text=summary_text,
                summarized_until_event_id=cursor,
            )
            remaining = remaining[boundary_index:]
            preview_messages = build_preview_messages(summary_text, remaining)
            estimated = _estimate_tokens_for_messages(preview_messages)
            if estimated <= target_threshold and len(remaining) <= keep_recent:
                break

        state = type("State", (), {"summary_text": summary_text, "summarized_until_event_id": cursor})()
        return remaining, state

    async def _run_private_summary_call(
        self,
        llm_client: Any,
        *,
        previous_summary: str,
        events: List[ConversationEventRecord],
        cfg: Dict[str, Any],
    ) -> str:
        dialogue = self._format_private_events_for_summary(events, cfg=cfg)
        user_payload = (
            f"当前摘要：\n{previous_summary.strip() or '(无)'}\n\n"
            f"新增私有记录：\n{dialogue}\n\n"
            "请输出更新后的摘要（只输出摘要文本）。"
        )
        system_role = self._system_role_for_client(llm_client)
        messages = [
            {"role": system_role, "content": PRIVATE_CONTEXT_SUMMARY_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        try:
            response = await llm_client.chat(messages)
        except Exception:
            return ""
        content = response.get("content")
        return str(content or "").strip()

    def _format_private_events_for_summary(
        self,
        events: List[ConversationEventRecord],
        *,
        cfg: Dict[str, Any],
    ) -> str:
        trunc_cfg = cfg.get("truncation", {})
        lines: List[str] = []
        for event in events:
            kind = str(event.kind or "")
            if kind == "tool_call":
                tool_name = str(event.tool_name or "")
                args = event.content.get("arguments", {})
                text = _coerce_text(args)
                text, _ = _truncate_text_middle(text, trunc_cfg)
                lines.append(f"Tool call: {tool_name}\n{text}".strip())
            elif kind == "tool_result":
                tool_name = str(event.tool_name or "")
                output = _coerce_text(event.content.get("output"))
                error = _coerce_text(event.content.get("error"))
                body = error.strip() if event.ok is False and error.strip() else output
                body, _ = _truncate_text_middle(body, trunc_cfg)
                lines.append(f"Tool result: {tool_name} ok={bool(event.ok)}\n{body}".strip())
        return "\n\n".join(lines).strip()

    def _render_message_center_history(
        self,
        records: List[MessageCenterEventRecord],
    ) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        for rec in records:
            kind = rec.kind
            sender = rec.sender_id
            topic = rec.topic
            ok = rec.ok
            payload = rec.payload or {}

            if kind == "rpc_request":
                text = _coerce_text(payload.get("content")) or _safe_json(payload)
                header = f"[RPC request] from {sender} topic={topic}"
                rendered.append({"role": "user", "content": f"{header}\n{text}".strip()})
                continue

            if kind == "rpc_response":
                text = _coerce_text(payload.get("reply")) or _coerce_text(payload.get("result")) or _safe_json(payload)
                header = f"[RPC response] from {sender} topic={topic} ok={ok}"
                rendered.append({"role": "assistant", "content": f"{header}\n{text}".strip()})
                continue

            if kind == "event":
                text = _coerce_text(payload.get("text")) or _safe_json(payload)
                header = f"[Event] from {sender} topic={topic}"
                rendered.append({"role": "assistant", "content": f"{header}\n{text}".strip()})
                continue

        return rendered

    def _render_private_events(self, events: List[ConversationEventRecord]) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        for event in events:
            kind = str(event.kind or "")
            if kind == "tool_call":
                tool_name = str(event.tool_name or "")
                args = event.content.get("arguments", {})
                rendered.append(
                    {
                        "role": "assistant",
                        "content": f"[Tool call] {tool_name}\n{_coerce_text(args)}",
                    }
                )
                continue
            if kind == "tool_result":
                tool_name = str(event.tool_name or "")
                output = _coerce_text(event.content.get("output"))
                error = _coerce_text(event.content.get("error"))
                if event.ok is False and error.strip():
                    rendered.append(
                        {
                            "role": "assistant",
                            "content": f"[Tool error] {tool_name}\n{error}",
                        }
                    )
                    continue
                if output.strip():
                    rendered.append(
                        {
                            "role": "assistant",
                            "content": f"[Tool result] {tool_name}\n{output}",
                        }
                    )
                continue
        return rendered

    async def _build_system_prompt(
        self,
        request,
        *,
        default_system_prompt: str,
        tool_policy_text: str,
    ) -> str:
        parts: List[str] = []
        base = (default_system_prompt or "").strip()
        if base:
            parts.append(base)
        policy = (tool_policy_text or "").strip()
        if policy:
            parts.append(policy)
        session_id = getattr(request, "session_id", None)
        if session_id:
            session = await self.session_repository.get(str(session_id))
            if session and session.system_prompt:
                parts.append(str(session.system_prompt).strip())
        req_prompt = getattr(request, "system_prompt", None)
        if req_prompt:
            parts.append(str(req_prompt).strip())
        return "\n\n".join([item for item in parts if item]).strip()

    def _render_inline_history(self, history: Any, max_history: int) -> List[Dict[str, Any]]:
        if not isinstance(history, list):
            return []
        items = history[-max_history:] if max_history > 0 else list(history)
        rendered: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if content is None:
                continue
            rendered.append({"role": role, "content": deepcopy(content)})
        return rendered

    def _system_role_for_client(self, llm_client: Any) -> str:
        profile = str(getattr(getattr(llm_client, "config", None), "api_profile", None) or "openai").lower()
        return "developer" if profile == "openai" else "system"

    def _resolve_cfg(self, override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        app_cfg = get_app_config()
        context_cfg = app_cfg.get("context", {}) if isinstance(app_cfg, dict) else {}
        trace_cfg = app_cfg.get("trace", {}) if isinstance(app_cfg, dict) else {}
        cfg: Dict[str, Any] = {
            "context": context_cfg if isinstance(context_cfg, dict) else {},
            "trace": trace_cfg if isinstance(trace_cfg, dict) else {},
            "truncation": (context_cfg.get("truncation", {}) if isinstance(context_cfg, dict) else {}) or {},
        }
        if isinstance(override, dict):
            cfg.update(deepcopy(override))
        return cfg

    def _resolve_prompt_budget(self, llm_client: Any, cfg: Dict[str, Any]) -> tuple[int, int]:
        max_context = int(getattr(getattr(llm_client, "config", None), "max_context_tokens", 0) or 0)
        if max_context <= 0:
            max_context = 200000
        max_output = int(getattr(getattr(llm_client, "config", None), "max_tokens", 0) or 0)
        safety = int(cfg.get("context", {}).get("budget_safety_tokens", 256) or 256)
        prompt_budget = max(512, max_context - max(0, max_output) - safety)
        return max_context, prompt_budget

    def _find_drop_index(self, messages: List[Dict[str, Any]]) -> Optional[int]:
        if not messages:
            return None
        for idx, msg in enumerate(messages):
            role = str(msg.get("role") or "")
            if role in {"system", "developer"}:
                continue
            content = _coerce_text(msg.get("content"))
            if idx == 1 and content.startswith(PRIVATE_CONTEXT_SUMMARY_MARKER):
                continue
            return idx
        return None


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)
