from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class AgentPromptState:
    session_id: str
    agent_id: str
    summary_text: str
    summarized_until_event_id: int
    updated_at: str


def _row_to_state(row) -> AgentPromptState:
    return AgentPromptState(
        session_id=str(row["session_id"]),
        agent_id=str(row["agent_id"]),
        summary_text=str(row["summary_text"] or ""),
        summarized_until_event_id=int(row["summarized_until_event_id"] or 0),
        updated_at=str(row["updated_at"]),
    )


class AgentPromptStateRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def get(self, session_id: str, agent_id: str) -> AgentPromptState | None:
        row = await self.store.fetchone(
            """
            SELECT session_id, agent_id, summary_text, summarized_until_event_id, updated_at
            FROM agent_prompt_state
            WHERE session_id = ? AND agent_id = ?
            """.strip(),
            (session_id, agent_id),
        )
        if row is None:
            return None
        return _row_to_state(row)

    async def get_or_create(self, session_id: str, agent_id: str) -> AgentPromptState:
        existing = await self.get(session_id, agent_id)
        if existing is not None:
            return existing
        now = utc_now_iso()
        try:
            await self.store.execute(
                """
                INSERT INTO agent_prompt_state
                  (session_id, agent_id, summary_text, summarized_until_event_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """.strip(),
                (session_id, agent_id, "", 0, now),
                commit=True,
            )
        except sqlite3.IntegrityError:
            pass
        created = await self.get(session_id, agent_id)
        if created is None:
            raise RuntimeError("failed to create agent_prompt_state row")
        return created

    async def update(
        self,
        session_id: str,
        agent_id: str,
        *,
        summary_text: str,
        summarized_until_event_id: int,
    ) -> AgentPromptState:
        now = utc_now_iso()
        await self.store.execute(
            """
            UPDATE agent_prompt_state
            SET summary_text = ?, summarized_until_event_id = ?, updated_at = ?
            WHERE session_id = ? AND agent_id = ?
            """.strip(),
            (summary_text, int(summarized_until_event_id), now, session_id, agent_id),
            commit=True,
        )
        updated = await self.get(session_id, agent_id)
        if updated is None:
            raise RuntimeError("failed to update agent_prompt_state row")
        return updated

