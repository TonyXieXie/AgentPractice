from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.execution.message_utils import (
    get_session_id,
    render_private_event_entry,
    render_shared_fact_entry,
    render_speaker_line,
)
from agents.execution.prompt_ir import PromptIR
from agents.message import AgentMessage
from observation.center import ObservationCenter
from observation.facts import PrivateExecutionEvent, SharedFact
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.agent_prompt_state_repository import AgentPromptStateRepository
from repositories.prompt_trace_repository import PromptTraceRepository
from repositories.session_repository import SessionRepository
from repositories.shared_fact_repository import SharedFactRepository


PRIVATE_CONTEXT_SUMMARY_PROMPT = (
    "你是执行过程摘要助手。请将该 Agent 的私有执行过程压缩为可供后续继续工作的简明摘要。\n"
    "- 只总结工具调用结果、关键中间结论、产生/修改的文件路径、命令、错误与修复尝试\n"
    "- 不要复述 shared 对话原文（shared facts 已经单独保存）\n"
    "- 如果有产物，只记录路径/索引，不要粘贴大段内容\n"
    "- 输出纯摘要文本，不要添加标题或前缀"
)


DEFAULT_BUDGET_CFG: Dict[str, Any] = {
    "context": {
        "max_shared_messages": 2000,
        "compression_enabled": True,
        "keep_recent_events": 20,
        "compress_start_pct": 75,
        "compress_target_pct": 55,
        "budget_safety_tokens": 512,
    },
    "trace": {"enabled": True},
    "truncation": {
        "enabled": True,
        "threshold_chars": 4000,
        "head_chars": 800,
        "tail_chars": 800,
    },
    "min_keep_messages": 4,
    "max_unsummarized_events": 2000,
}


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_coerce_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "summary_text", "value", "output", "error", "reply"):
            if key in value and value[key] is not None:
                return _coerce_text(value[key])
    return str(value)


def _iter_message_text_parts(message: Dict[str, Any]) -> List[str]:
    parts: List[str] = []
    content = message.get("content")
    if content is not None:
        parts.append(_coerce_text(content))
    for key in ("name", "arguments", "output", "summary"):
        if key in message and message[key] is not None:
            parts.append(_coerce_text(message[key]))
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if isinstance(function, dict):
            parts.append(_coerce_text(function.get("name")))
            parts.append(_coerce_text(function.get("arguments")))
    return [part for part in parts if part]


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
        for part in _iter_message_text_parts(msg):
            total += _estimate_tokens_for_text(part)
    return total


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
    truncated = f"{head_text}[TRUNCATED]({omitted} chars omitted){tail_text}"
    return truncated, True


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class AgentMemory:
    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        shared_fact_repository: SharedFactRepository,
        agent_private_event_repository: AgentPrivateEventRepository,
        agent_prompt_state_repository: AgentPromptStateRepository,
        prompt_trace_repository: PromptTraceRepository,
        observation_center: Optional[ObservationCenter] = None,
    ) -> None:
        self.session_repository = session_repository
        self.shared_fact_repository = shared_fact_repository
        self.agent_private_event_repository = agent_private_event_repository
        self.agent_prompt_state_repository = agent_prompt_state_repository
        self.prompt_trace_repository = prompt_trace_repository
        self.observation_center = observation_center

    async def build_history_for_agent(
        self,
        message: AgentMessage,
        *,
        agent_id: str,
        llm_client: Any,
        max_history_events: int = 20,
        budget_cfg: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        session_id = get_session_id(message)
        if not session_id or not agent_id:
            return []
        cfg = self._resolve_cfg(budget_cfg)
        shared_records = await self.shared_fact_repository.list(
            session_id,
            after_seq=0,
            limit=int(cfg.get("context", {}).get("max_shared_messages", 2000) or 2000),
            run_id=None,
        )
        shared_records = [
            fact for fact in shared_records if str(fact.message_id or "") != str(message.id or "")
        ]

        state = await self.agent_prompt_state_repository.get_or_create(session_id, agent_id)
        cursor = int(state.summarized_until_event_id or 0)
        private_events = await self.agent_private_event_repository.list(
            session_id,
            owner_agent_id=agent_id,
            after_id=cursor,
            limit=int(cfg.get("max_unsummarized_events", 2000) or 2000),
        )

        if cfg.get("context", {}).get("compression_enabled", True) and llm_client is not None:
            state, private_events = await self._maybe_rollup_private_summary(
                session_id=session_id,
                agent_id=agent_id,
                llm_client=llm_client,
                state=state,
                private_events=private_events,
                cfg=cfg,
            )

        timeline: List[tuple[str, int, Dict[str, Any]]] = []
        for fact in shared_records:
            rendered = render_shared_fact_entry(fact)
            if rendered is not None:
                timeline.append((fact.created_at, 0, rendered))

        summary_text = str(state.summary_text or "").strip()
        if summary_text:
            timeline.append(
                (
                    state.updated_at,
                    1,
                    {"role": "assistant", "content": render_speaker_line(agent_id, summary_text)},
                )
            )

        for event in private_events:
            rendered = render_private_event_entry(event, current_agent_id=agent_id)
            if rendered is not None:
                timeline.append((event.created_at, 1, rendered))

        timeline.sort(key=lambda item: (item[0], item[1]))
        messages = [item[2] for item in timeline]
        if max_history_events > 0 and len(messages) > max_history_events:
            first_user = messages[0] if messages and messages[0].get("role") == "user" else None
            if first_user is not None and max_history_events > 1:
                tail = messages[-(max_history_events - 1) :]
                messages = [first_user, *tail]
            else:
                messages = messages[-max_history_events:]
        return messages

    async def ensure_budget_for_view(
        self,
        prompt_ir: PromptIR,
        *,
        llm_client: Any,
        session_id: Optional[str],
        agent_id: Optional[str] = None,
        run_id: Optional[str],
        phase: str,
        actions: Optional[Dict[str, Any]] = None,
        iteration: Optional[int] = None,
    ) -> PromptIR:
        cfg = self._cfg_from_prompt_ir(prompt_ir)
        trace_root = prompt_ir.trace.setdefault("actions", [])
        trace_context = actions or {"phase": phase}
        trace_context["phase"] = phase
        if iteration is not None:
            trace_context["iteration"] = iteration
        trace_context.setdefault("events", [])
        max_context_tokens, prompt_budget = self._resolve_prompt_budget(llm_client, cfg)
        trunc_cfg = cfg.get("truncation", {})

        for idx, msg in enumerate(prompt_ir.messages):
            content = msg.get("content")
            if content is None:
                continue
            truncated, changed = _truncate_text_middle(_coerce_text(content), trunc_cfg)
            if changed:
                msg["content"] = truncated
                trace_context["events"].append(
                    {"type": "truncate_message", "message_index": idx}
                )

        estimated = _estimate_tokens_for_messages(prompt_ir.messages)
        min_keep = max(2, int(cfg.get("min_keep_messages", 4) or 4))
        while estimated > prompt_budget and len(prompt_ir.messages) > min_keep:
            drop_index = self._find_drop_index(prompt_ir.messages)
            if drop_index is None:
                break
            prompt_ir.messages.pop(drop_index)
            trace_context["events"].append(
                {"type": "drop_message", "message_index": drop_index}
            )
            estimated = _estimate_tokens_for_messages(prompt_ir.messages)

        prompt_ir.budget = {
            "max_context_tokens": max_context_tokens,
            "prompt_budget": prompt_budget,
            "estimated_prompt_tokens": estimated,
            "rendered_message_count": len(prompt_ir.messages),
            "phase": phase,
        }
        if iteration is not None:
            prompt_ir.budget["iteration"] = iteration

        trace_root.append(deepcopy(trace_context))
        if session_id and cfg.get("trace", {}).get("enabled", True):
            try:
                await self.prompt_trace_repository.append(
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    llm_model=str(
                        getattr(getattr(llm_client, "config", None), "model", None) or ""
                    ),
                    max_context_tokens=max_context_tokens,
                    prompt_budget=prompt_budget,
                    estimated_prompt_tokens=estimated,
                    rendered_message_count=len(prompt_ir.messages),
                    request_messages=deepcopy(prompt_ir.messages),
                    actions=deepcopy(trace_context),
                )
            except Exception:
                pass
        return prompt_ir

    async def _maybe_rollup_private_summary(
        self,
        *,
        session_id: str,
        agent_id: str,
        llm_client: Any,
        state,
        private_events: List[PrivateExecutionEvent],
        cfg: Dict[str, Any],
    ):
        keep_recent = max(
            1, int(cfg.get("context", {}).get("keep_recent_events", 20) or 20)
        )
        preview_messages = [
            rendered
            for event in private_events
            if (rendered := render_private_event_entry(event, current_agent_id=agent_id))
            is not None
        ]
        estimated = _estimate_tokens_for_messages(preview_messages)
        max_context_tokens, prompt_budget = self._resolve_prompt_budget(llm_client, cfg)
        start_pct = int(cfg.get("context", {}).get("compress_start_pct", 75) or 75)
        start_threshold = min(int((max_context_tokens * start_pct) / 100.0), prompt_budget)
        if estimated <= start_threshold and len(private_events) <= keep_recent:
            return state, private_events

        boundary_index = max(1, len(private_events) - keep_recent)
        to_summarize = private_events[:boundary_index]
        remaining = private_events[boundary_index:]
        if not to_summarize:
            return state, private_events
        summary_text = await self._run_private_summary_call(
            llm_client,
            previous_summary=str(state.summary_text or ""),
            events=to_summarize,
            agent_id=agent_id,
        )
        if not summary_text.strip():
            return state, private_events
        summary_event = await self._append_private_summary_event(
            session_id=session_id,
            agent_id=agent_id,
            run_id=remaining[-1].run_id if remaining else to_summarize[-1].run_id,
            trigger_fact_id=remaining[-1].trigger_fact_id if remaining else to_summarize[-1].trigger_fact_id,
            summary_text=summary_text.strip(),
            covered_until_private_event_id=to_summarize[-1].private_event_id,
        )
        state = await self.agent_prompt_state_repository.update(
            session_id,
            agent_id,
            summary_text=summary_text.strip(),
            summarized_until_event_id=summary_event.private_event_id,
        )
        return state, remaining

    async def _append_private_summary_event(
        self,
        *,
        session_id: str,
        agent_id: str,
        run_id: Optional[str],
        trigger_fact_id: Optional[str],
        summary_text: str,
        covered_until_private_event_id: int,
    ) -> PrivateExecutionEvent:
        if self.observation_center is not None:
            return await self.observation_center.append_private_event(
                session_id=session_id,
                owner_agent_id=agent_id,
                run_id=run_id,
                trigger_fact_id=trigger_fact_id,
                kind="private_summary",
                payload_json={
                    "summary_text": summary_text,
                    "covered_until_private_event_id": covered_until_private_event_id,
                },
            )
        return await self.agent_private_event_repository.append(
            session_id=session_id,
            owner_agent_id=agent_id,
            run_id=run_id,
            trigger_fact_id=trigger_fact_id,
            kind="private_summary",
            payload_json={
                "summary_text": summary_text,
                "covered_until_private_event_id": covered_until_private_event_id,
            },
        )

    async def _run_private_summary_call(
        self,
        llm_client: Any,
        *,
        previous_summary: str,
        events: List[PrivateExecutionEvent],
        agent_id: str,
    ) -> str:
        dialogue = "\n\n".join(
            rendered["content"]
            for event in events
            if (rendered := render_private_event_entry(event, current_agent_id=agent_id))
            is not None
        )
        user_payload = (
            f"当前摘要：\n{previous_summary.strip() or '(无)'}\n\n"
            f"新增私有记录：\n{dialogue}\n\n"
            "请输出更新后的摘要（只输出摘要文本）。"
        )
        messages = [
            {"role": self._system_role_for_client(llm_client), "content": PRIVATE_CONTEXT_SUMMARY_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        try:
            response = await llm_client.chat(PromptIR(messages=messages, budget={}, trace={}))
        except Exception:
            return ""
        return str(response.get("content") or "").strip()

    def _resolve_cfg(self, budget_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return _deep_merge(DEFAULT_BUDGET_CFG, budget_cfg or {})

    def _cfg_from_prompt_ir(self, prompt_ir: PromptIR) -> Dict[str, Any]:
        cfg = prompt_ir.trace.get("cfg") if isinstance(prompt_ir.trace, dict) else None
        if isinstance(cfg, dict):
            return cfg
        cfg = self._resolve_cfg(None)
        prompt_ir.trace.setdefault("cfg", cfg)
        return cfg

    def _resolve_prompt_budget(self, llm_client: Any, cfg: Dict[str, Any]) -> tuple[int, int]:
        config = getattr(llm_client, "config", None)
        max_context_tokens = int(getattr(config, "max_context_tokens", 16000) or 16000)
        max_tokens = int(getattr(config, "max_tokens", 2000) or 2000)
        safety_tokens = int(
            cfg.get("context", {}).get("budget_safety_tokens", 512) or 512
        )
        prompt_budget = max(512, max_context_tokens - max_tokens - safety_tokens)
        return max_context_tokens, prompt_budget

    def _find_drop_index(self, messages: List[Dict[str, Any]]) -> Optional[int]:
        if len(messages) <= 2:
            return None
        for idx in range(1, len(messages) - 1):
            if messages[idx].get("role") != "system":
                return idx
        return None

    def _system_role_for_client(self, llm_client: Any) -> str:
        api_format = str(
            getattr(getattr(llm_client, "config", None), "api_format", "") or ""
        ).strip()
        return "developer" if api_format == "openai_responses" else "system"
