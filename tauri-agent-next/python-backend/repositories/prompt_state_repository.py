from __future__ import annotations

from dataclasses import dataclass

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class PromptStateRecord:
    session_id: str
    summary_text: str
    summarized_until_event_id: int
    updated_at: str


def _row_to_record(row) -> PromptStateRecord:
    return PromptStateRecord(
        session_id=str(row["session_id"]),
        summary_text=str(row["summary_text"] or ""),
        summarized_until_event_id=int(row["summarized_until_event_id"] or 0),
        updated_at=str(row["updated_at"]),
    )


class PromptStateRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def get(self, session_id: str) -> PromptStateRecord | None:
        row = await self.store.fetchone(
            """
            SELECT session_id, summary_text, summarized_until_event_id, updated_at
            FROM prompt_state
            WHERE session_id = ?
            """.strip(),
            (session_id,),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def get_or_create(self, session_id: str) -> PromptStateRecord:
        existing = await self.get(session_id)
        if existing is not None:
            return existing
        now = utc_now_iso()
        await self.store.execute(
            """
            INSERT OR IGNORE INTO prompt_state (session_id, summary_text, summarized_until_event_id, updated_at)
            VALUES (?, '', 0, ?)
            """.strip(),
            (session_id, now),
            commit=True,
        )
        created = await self.get(session_id)
        if created is None:
            raise RuntimeError("failed to create prompt_state row")
        return created

    async def update(
        self,
        session_id: str,
        *,
        summary_text: str,
        summarized_until_event_id: int,
    ) -> PromptStateRecord:
        now = utc_now_iso()
        await self.store.execute(
            """
            UPDATE prompt_state
            SET summary_text = ?, summarized_until_event_id = ?, updated_at = ?
            WHERE session_id = ?
            """.strip(),
            (summary_text, int(summarized_until_event_id), now, session_id),
            commit=True,
        )
        record = await self.get(session_id)
        if record is None:
            raise RuntimeError("prompt_state row missing after update")
        return record
