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
    llm_model: Optional[str]
    max_context_tokens: int
    prompt_budget: int
    estimated_prompt_tokens: int
    rendered_message_count: int
    actions: Dict[str, Any]
    created_at: str


def _row_to_record(row) -> PromptTraceRecord:
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
        llm_model=row["llm_model"],
        max_context_tokens=int(row["max_context_tokens"]),
        prompt_budget=int(row["prompt_budget"]),
        estimated_prompt_tokens=int(row["estimated_prompt_tokens"]),
        rendered_message_count=int(row["rendered_message_count"]),
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
        llm_model: Optional[str],
        max_context_tokens: int,
        prompt_budget: int,
        estimated_prompt_tokens: int,
        rendered_message_count: int,
        actions: Dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        trace_id = await self.store.execute_insert(
            """
            INSERT INTO prompt_traces
              (session_id, run_id, llm_model, max_context_tokens, prompt_budget,
               estimated_prompt_tokens, rendered_message_count, actions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                run_id,
                llm_model,
                int(max_context_tokens),
                int(prompt_budget),
                int(estimated_prompt_tokens),
                int(rendered_message_count),
                json.dumps(actions, ensure_ascii=False),
                now,
            ),
        )
        if trace_id <= 0:
            raise RuntimeError("failed to insert prompt trace")
        return trace_id

    async def get_latest(self, session_id: str) -> Optional[PromptTraceRecord]:
        row = await self.store.fetchone(
            """
            SELECT id, session_id, run_id, llm_model, max_context_tokens, prompt_budget,
                   estimated_prompt_tokens, rendered_message_count, actions_json, created_at
            FROM prompt_traces
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (session_id,),
        )
        if row is None:
            return None
        return _row_to_record(row)
