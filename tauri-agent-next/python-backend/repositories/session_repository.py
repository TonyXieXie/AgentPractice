from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class SessionRecord:
    id: str
    title: Optional[str]
    system_prompt: Optional[str]
    work_path: Optional[str]
    llm_config: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str


def _coerce_llm_config(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _row_to_record(row) -> SessionRecord:
    return SessionRecord(
        id=str(row["id"]),
        title=row["title"],
        system_prompt=row["system_prompt"],
        work_path=row["work_path"],
        llm_config=_coerce_llm_config(row["llm_config_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class SessionRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        row = await self.store.fetchone(
            """
            SELECT id, title, system_prompt, work_path, llm_config_json, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """.strip(),
            (session_id,),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def create(
        self,
        *,
        session_id: str,
        title: Optional[str] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
    ) -> SessionRecord:
        now = utc_now_iso()
        llm_config_json = (
            json.dumps(llm_config, ensure_ascii=False) if llm_config is not None else None
        )
        await self.store.execute(
            """
            INSERT INTO sessions (id, title, system_prompt, work_path, llm_config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                title,
                system_prompt,
                work_path,
                llm_config_json,
                now,
                now,
            ),
            commit=True,
        )
        created = await self.get(session_id)
        if created is None:
            raise RuntimeError("failed to create session")
        return created

    async def update_defaults(
        self,
        session_id: str,
        *,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
    ) -> Optional[SessionRecord]:
        llm_config_json = (
            json.dumps(llm_config, ensure_ascii=False) if llm_config is not None else None
        )
        now = utc_now_iso()
        await self.store.execute(
            """
            UPDATE sessions
            SET
              system_prompt = COALESCE(?, system_prompt),
              work_path = COALESCE(?, work_path),
              llm_config_json = COALESCE(?, llm_config_json),
              updated_at = ?
            WHERE id = ?
            """.strip(),
            (
                system_prompt,
                work_path,
                llm_config_json,
                now,
                session_id,
            ),
            commit=True,
        )
        return await self.get(session_id)
