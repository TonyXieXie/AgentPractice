from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class AgentInstanceRecord:
    id: str
    session_id: str
    agent_type: str
    profile_id: Optional[str]
    role: str
    display_name: Optional[str]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


def _row_to_record(row) -> AgentInstanceRecord:
    raw_metadata = str(row["metadata_json"] or "")
    try:
        metadata = json.loads(raw_metadata) if raw_metadata else {}
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return AgentInstanceRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        agent_type=str(row["agent_type"]),
        profile_id=row["profile_id"],
        role=str(row["role"]),
        display_name=row["display_name"],
        metadata=metadata,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class AgentInstanceRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def get(self, agent_id: str) -> AgentInstanceRecord | None:
        row = await self.store.fetchone(
            """
            SELECT id, session_id, agent_type, profile_id, role, display_name, metadata_json, created_at, updated_at
            FROM agent_instances
            WHERE id = ?
            """.strip(),
            (agent_id,),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def list_by_session(self, session_id: str) -> list[AgentInstanceRecord]:
        rows = await self.store.fetchall(
            """
            SELECT id, session_id, agent_type, profile_id, role, display_name, metadata_json, created_at, updated_at
            FROM agent_instances
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            """.strip(),
            (session_id,),
        )
        return [_row_to_record(row) for row in rows]

    async def get_primary(self, session_id: str, agent_type: str) -> AgentInstanceRecord | None:
        row = await self.store.fetchone(
            """
            SELECT id, session_id, agent_type, profile_id, role, display_name, metadata_json, created_at, updated_at
            FROM agent_instances
            WHERE session_id = ? AND agent_type = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """.strip(),
            (session_id, str(agent_type)),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def get_or_create_primary(
        self,
        session_id: str,
        agent_type: str,
        *,
        profile_id: Optional[str] = "default",
        display_name: Optional[str] = None,
    ) -> AgentInstanceRecord:
        existing = await self.get_primary(session_id, agent_type)
        if existing is not None:
            return existing

        return await self.create(
            session_id=session_id,
            agent_type=agent_type,
            profile_id=profile_id,
            role=str(agent_type),
            display_name=display_name,
        )

    async def create(
        self,
        *,
        session_id: str,
        agent_type: str,
        profile_id: Optional[str],
        role: Optional[str] = None,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentInstanceRecord:
        now = utc_now_iso()
        agent_id = uuid4().hex
        resolved_role = str(role or agent_type)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self.store.execute(
            """
            INSERT INTO agent_instances
              (id, session_id, agent_type, profile_id, role, display_name, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                agent_id,
                session_id,
                str(agent_type),
                profile_id,
                resolved_role,
                display_name,
                metadata_json,
                now,
                now,
            ),
            commit=True,
        )
        created = await self.get(agent_id)
        if created is None:
            raise RuntimeError("failed to create agent_instances row")
        return created
