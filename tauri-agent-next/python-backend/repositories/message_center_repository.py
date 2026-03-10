from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.message import AgentMessage
from repositories.sqlite_store import SqliteStore


@dataclass(slots=True)
class MessageCenterEventRecord:
    id: int
    session_id: str
    run_id: Optional[str]
    viewer_agent_id: str
    message_id: str
    seq: Optional[int]
    kind: str
    delivery: str
    topic: str
    sender_id: str
    target_id: Optional[str]
    correlation_id: Optional[str]
    ok: Optional[bool]
    visibility: str
    level: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    created_at: str


def _row_to_record(row) -> MessageCenterEventRecord:
    raw_payload = str(row["payload_json"] or "")
    raw_metadata = str(row["metadata_json"] or "")
    try:
        payload = json.loads(raw_payload) if raw_payload else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        metadata = json.loads(raw_metadata) if raw_metadata else {}
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    ok_raw = row["ok"]
    ok: Optional[bool]
    if ok_raw is None:
        ok = None
    else:
        ok = bool(int(ok_raw))

    seq_raw = row["seq"]
    seq: Optional[int] = None if seq_raw is None else int(seq_raw)

    return MessageCenterEventRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        viewer_agent_id=str(row["viewer_agent_id"]),
        message_id=str(row["message_id"]),
        seq=seq,
        kind=str(row["kind"]),
        delivery=str(row["delivery"]),
        topic=str(row["topic"]),
        sender_id=str(row["sender_id"]),
        target_id=row["target_id"],
        correlation_id=row["correlation_id"],
        ok=ok,
        visibility=str(row["visibility"]),
        level=str(row["level"]),
        payload=payload,
        metadata=metadata,
        created_at=str(row["created_at"]),
    )


class MessageCenterRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append_visible_message(
        self,
        *,
        session_id: str,
        viewer_agent_id: str,
        message: AgentMessage,
    ) -> int:
        ok_value: Optional[int]
        if message.ok is None:
            ok_value = None
        else:
            ok_value = 1 if bool(message.ok) else 0

        payload_json = json.dumps(message.payload or {}, ensure_ascii=False)
        metadata_json = json.dumps(message.metadata or {}, ensure_ascii=False)

        row_id = await self.store.execute_insert(
            """
            INSERT INTO message_center_events
              (session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, topic,
               sender_id, target_id, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                message.run_id,
                viewer_agent_id,
                message.id,
                message.seq,
                message.kind,
                message.delivery,
                message.topic,
                message.sender_id,
                message.target_id,
                message.correlation_id,
                ok_value,
                message.visibility,
                message.level,
                payload_json,
                metadata_json,
                message.created_at,
            ),
        )
        if row_id <= 0:
            raise RuntimeError("failed to insert message_center event")
        return row_id

    async def list_latest_visible(
        self,
        session_id: str,
        viewer_agent_id: str,
        *,
        limit: int,
        exclude_message_id: Optional[str] = None,
    ) -> list[MessageCenterEventRecord]:
        if exclude_message_id:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, topic,
                       sender_id, target_id, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at
                FROM message_center_events
                WHERE session_id = ? AND viewer_agent_id = ? AND message_id <> ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, viewer_agent_id, exclude_message_id, int(limit)),
            )
        else:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, topic,
                       sender_id, target_id, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at
                FROM message_center_events
                WHERE session_id = ? AND viewer_agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, viewer_agent_id, int(limit)),
            )
        records = [_row_to_record(row) for row in rows]
        records.reverse()
        return records

