from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class PromptTraceRecord:
    id: int
    session_id: str
    run_id: Optional[str]
    agent_id: Optional[str]
    llm_model: Optional[str]
    max_context_tokens: int
    prompt_budget: int
    estimated_prompt_tokens: int
    rendered_message_count: int
    request_messages: list[dict[str, Any]]
    actions: Dict[str, Any]
    created_at: str


def _row_to_record(row) -> PromptTraceRecord:
    raw_messages = str(row["request_messages_json"] or "")
    try:
        request_messages = json.loads(raw_messages) if raw_messages else []
    except json.JSONDecodeError:
        request_messages = []
    if not isinstance(request_messages, list):
        request_messages = []
    normalized_messages = [
        dict(item)
        for item in request_messages
        if isinstance(item, dict)
    ]

    raw_actions = str(row["actions_json"] or "")
    try:
        actions = json.loads(raw_actions) if raw_actions else {}
    except json.JSONDecodeError:
        actions = {}
    if not isinstance(actions, dict):
        actions = {}
    return PromptTraceRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        llm_model=row["llm_model"],
        max_context_tokens=int(row["max_context_tokens"]),
        prompt_budget=int(row["prompt_budget"]),
        estimated_prompt_tokens=int(row["estimated_prompt_tokens"]),
        rendered_message_count=int(row["rendered_message_count"]),
        request_messages=normalized_messages,
        actions=actions,
        created_at=str(row["created_at"]),
    )


class PromptTraceRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append(
        self,
        *,
        session_id: str,
        run_id: Optional[str],
        agent_id: Optional[str] = None,
        llm_model: Optional[str],
        max_context_tokens: int,
        prompt_budget: int,
        estimated_prompt_tokens: int,
        rendered_message_count: int,
        request_messages: list[dict[str, Any]],
        actions: Dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        trace_id = await self.store.execute_insert(
            """
            INSERT INTO prompt_traces
              (session_id, run_id, agent_id, llm_model, max_context_tokens, prompt_budget,
               estimated_prompt_tokens, rendered_message_count, request_messages_json,
               actions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                run_id,
                agent_id,
                llm_model,
                int(max_context_tokens),
                int(prompt_budget),
                int(estimated_prompt_tokens),
                int(rendered_message_count),
                json.dumps(request_messages, ensure_ascii=False),
                json.dumps(actions, ensure_ascii=False),
                now,
            ),
        )
        if trace_id <= 0:
            raise RuntimeError("failed to insert prompt trace")
        return trace_id

    async def get_latest(
        self,
        session_id: str,
        *,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[PromptTraceRecord]:
        if agent_id is None and run_id is None:
            row = await self.store.fetchone(
                """
                SELECT id, session_id, run_id, agent_id, llm_model, max_context_tokens, prompt_budget,
                       estimated_prompt_tokens, rendered_message_count, request_messages_json,
                       actions_json, created_at
                FROM prompt_traces
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """.strip(),
                (session_id,),
            )
        elif agent_id is None:
            row = await self.store.fetchone(
                """
                SELECT id, session_id, run_id, agent_id, llm_model, max_context_tokens, prompt_budget,
                       estimated_prompt_tokens, rendered_message_count, request_messages_json,
                       actions_json, created_at
                FROM prompt_traces
                WHERE session_id = ? AND run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """.strip(),
                (session_id, run_id),
            )
        elif run_id is None:
            row = await self.store.fetchone(
                """
                SELECT id, session_id, run_id, agent_id, llm_model, max_context_tokens, prompt_budget,
                       estimated_prompt_tokens, rendered_message_count, request_messages_json,
                       actions_json, created_at
                FROM prompt_traces
                WHERE session_id = ? AND agent_id = ?
                ORDER BY id DESC
                LIMIT 1
                """.strip(),
                (session_id, agent_id),
            )
        else:
            row = await self.store.fetchone(
                """
                SELECT id, session_id, run_id, agent_id, llm_model, max_context_tokens, prompt_budget,
                       estimated_prompt_tokens, rendered_message_count, request_messages_json,
                       actions_json, created_at
                FROM prompt_traces
                WHERE session_id = ? AND agent_id = ? AND run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """.strip(),
                (session_id, agent_id, run_id),
            )
        if row is None:
            return None
        return _row_to_record(row)
