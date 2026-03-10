from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class ConversationEventRecord:
    id: int
    session_id: str
    run_id: Optional[str]
    agent_id: Optional[str]
    kind: str
    content: Dict[str, Any]
    tool_name: Optional[str]
    tool_call_id: Optional[str]
    ok: Optional[bool]
    created_at: str


def _row_to_record(row) -> ConversationEventRecord:
    raw_content = str(row["content_json"] or "")
    try:
        content = json.loads(raw_content) if raw_content else {}
    except json.JSONDecodeError:
        content = {}
    if not isinstance(content, dict):
        content = {}
    ok_raw = row["ok"]
    ok: Optional[bool]
    if ok_raw is None:
        ok = None
    else:
        ok = bool(int(ok_raw))
    return ConversationEventRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        kind=str(row["kind"]),
        content=content,
        tool_name=row["tool_name"],
        tool_call_id=row["tool_call_id"],
        ok=ok,
        created_at=str(row["created_at"]),
    )


class ConversationRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append_event(
        self,
        *,
        session_id: str,
        run_id: Optional[str],
        agent_id: Optional[str] = None,
        kind: str,
        content: Dict[str, Any],
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        ok: Optional[bool] = None,
    ) -> int:
        now = utc_now_iso()
        ok_value: Optional[int]
        if ok is None:
            ok_value = None
        else:
            ok_value = 1 if ok else 0
        event_id = await self.store.execute_insert(
            """
            INSERT INTO conversation_events
              (session_id, run_id, agent_id, kind, content_json, tool_name, tool_call_id, ok, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                run_id,
                agent_id,
                kind,
                json.dumps(content, ensure_ascii=False),
                tool_name,
                tool_call_id,
                ok_value,
                now,
            ),
        )
        if event_id <= 0:
            raise RuntimeError("failed to insert conversation event")
        return event_id

    async def list_events_after(
        self,
        session_id: str,
        *,
        agent_id: Optional[str] = None,
        after_id: int,
        limit: int,
    ) -> list[ConversationEventRecord]:
        if agent_id is None:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, agent_id, kind, content_json, tool_name, tool_call_id, ok, created_at
                FROM conversation_events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """.strip(),
                (session_id, int(after_id), int(limit)),
            )
        else:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, agent_id, kind, content_json, tool_name, tool_call_id, ok, created_at
                FROM conversation_events
                WHERE session_id = ? AND agent_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """.strip(),
                (session_id, agent_id, int(after_id), int(limit)),
            )
        return [_row_to_record(row) for row in rows]

    async def list_latest(
        self,
        session_id: str,
        *,
        agent_id: Optional[str] = None,
        limit: int,
    ) -> list[ConversationEventRecord]:
        if agent_id is None:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, agent_id, kind, content_json, tool_name, tool_call_id, ok, created_at
                FROM conversation_events
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, int(limit)),
            )
        else:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, agent_id, kind, content_json, tool_name, tool_call_id, ok, created_at
                FROM conversation_events
                WHERE session_id = ? AND agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, agent_id, int(limit)),
            )
        records = [_row_to_record(row) for row in rows]
        records.reverse()
        return records
