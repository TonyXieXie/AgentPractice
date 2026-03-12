from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.message import AgentMessage
from repositories.sqlite_store import SqliteStore


SHARED_VIEWER_AGENT_ID = "__shared__"


@dataclass(slots=True)
class MessageCenterEventRecord:
    id: int
    session_id: str
    run_id: Optional[str]
    viewer_agent_id: str
    message_id: str
    seq: Optional[int]
    message_type: str
    object_type: str
    rpc_phase: Optional[str]
    topic: str
    sender_id: str
    target_agent_id: Optional[str]
    target_profile: Optional[str]
    correlation_id: Optional[str]
    ok: Optional[bool]
    visibility: str
    level: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    created_at: str

    @property
    def kind(self) -> str:
        if self.message_type == "event":
            return "event"
        if self.rpc_phase == "response":
            return "rpc_response"
        return "rpc_request"

    @property
    def delivery(self) -> str:
        return "broadcast" if self.object_type == "broadcast" else "unicast"

    @property
    def target_id(self) -> Optional[str]:
        return self.target_agent_id


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

    legacy_kind = str(row["kind"] or "")
    raw_message_type = str(row["message_type"] or "").strip()
    raw_object_type = str(row["object_type"] or "").strip()
    raw_rpc_phase = row["rpc_phase"]
    if not raw_message_type:
        raw_message_type = "event" if legacy_kind == "event" else "rpc"
    if not raw_object_type:
        raw_object_type = str(row["delivery"] or "").strip()
    if raw_object_type == "unicast":
        raw_object_type = "target"
    if not raw_rpc_phase and legacy_kind == "rpc_request":
        raw_rpc_phase = "request"
    if not raw_rpc_phase and legacy_kind == "rpc_response":
        raw_rpc_phase = "response"

    return MessageCenterEventRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        viewer_agent_id=str(row["viewer_agent_id"]),
        message_id=str(row["message_id"]),
        seq=seq,
        message_type=raw_message_type,
        object_type=raw_object_type,
        rpc_phase=raw_rpc_phase,
        topic=str(row["topic"]),
        sender_id=str(row["sender_id"]),
        target_agent_id=row["target_agent_id"] or row["target_id"],
        target_profile=row["target_profile"],
        correlation_id=row["correlation_id"],
        ok=ok,
        visibility=str(row["visibility"]),
        level=str(row["level"]),
        payload=payload,
        metadata=metadata,
        created_at=str(row["created_at"]),
    )


def _record_to_message(record: MessageCenterEventRecord) -> AgentMessage:
    target: Optional[AgentMessage.TargetRef] = None
    if record.object_type != "broadcast":
        target_id = str(record.target_agent_id or "").strip() or None
        target_profile = str(record.target_profile or "").strip() or None
        if target_id or target_profile:
            target = AgentMessage.TargetRef(
                agent_id=target_id,
                profile_id=(None if target_id else target_profile),
            )
    return AgentMessage(
        id=record.message_id,
        message_type=record.message_type,
        object_type=record.object_type,
        rpc_phase=record.rpc_phase,
        topic=record.topic,
        sender_id=record.sender_id,
        target=target,
        correlation_id=record.correlation_id,
        run_id=record.run_id,
        session_id=record.session_id,
        seq=record.seq,
        visibility=record.visibility,
        level=record.level,
        ok=record.ok,
        payload=dict(record.payload or {}),
        metadata=dict(record.metadata or {}),
        created_at=record.created_at,
    )


class MessageCenterRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append_shared_message(
        self,
        *,
        session_id: str,
        message: AgentMessage,
    ) -> int:
        ok_value: Optional[int]
        if message.ok is None:
            ok_value = None
        else:
            ok_value = 1 if bool(message.ok) else 0

        payload_json = json.dumps(message.payload or {}, ensure_ascii=False)
        metadata_json = json.dumps(message.metadata or {}, ensure_ascii=False)
        requested_target_profile = None
        if isinstance(message.metadata, dict):
            value = message.metadata.get("target_profile")
            if value is not None:
                requested_target_profile = str(value).strip() or None
        resolved_target_profile = message.target_profile or requested_target_profile

        row_id = await self.store.execute_insert(
            """
            INSERT INTO message_center_events
              (session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, message_type, object_type, rpc_phase, topic,
               sender_id, target_id, target_agent_id, target_profile, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                message.run_id,
                SHARED_VIEWER_AGENT_ID,
                message.id,
                message.seq,
                message.kind,
                message.delivery,
                message.message_type,
                message.object_type,
                message.rpc_phase,
                message.topic,
                message.sender_id,
                message.target_id,
                message.target_id,
                resolved_target_profile,
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

    async def append_visible_message(
        self,
        *,
        session_id: str,
        viewer_agent_id: str,
        message: AgentMessage,
    ) -> int:
        return await self.append_shared_message(session_id=session_id, message=message)

    async def list_latest_shared(
        self,
        session_id: str,
        *,
        limit: int,
        exclude_message_id: Optional[str] = None,
    ) -> list[MessageCenterEventRecord]:
        if exclude_message_id:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, message_type, object_type, rpc_phase, topic,
                       sender_id, target_id, target_agent_id, target_profile, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at
                FROM message_center_events
                WHERE session_id = ? AND viewer_agent_id = ? AND message_id <> ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, SHARED_VIEWER_AGENT_ID, exclude_message_id, int(limit)),
            )
        else:
            rows = await self.store.fetchall(
                """
                SELECT id, session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, message_type, object_type, rpc_phase, topic,
                       sender_id, target_id, target_agent_id, target_profile, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at
                FROM message_center_events
                WHERE session_id = ? AND viewer_agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (session_id, SHARED_VIEWER_AGENT_ID, int(limit)),
            )
        records = [_row_to_record(row) for row in rows]
        records.reverse()
        return records

    async def list_latest_visible(
        self,
        session_id: str,
        viewer_agent_id: str,
        *,
        limit: int,
        exclude_message_id: Optional[str] = None,
    ) -> list[MessageCenterEventRecord]:
        return await self.list_latest_shared(
            session_id,
            limit=limit,
            exclude_message_id=exclude_message_id,
        )

    async def get_shared_message(
        self,
        session_id: str,
        message_id: str,
    ) -> Optional[AgentMessage]:
        row = await self.store.fetchone(
            """
            SELECT id, session_id, run_id, viewer_agent_id, message_id, seq, kind, delivery, message_type, object_type, rpc_phase, topic,
                   sender_id, target_id, target_agent_id, target_profile, correlation_id, ok, visibility, level, payload_json, metadata_json, created_at
            FROM message_center_events
            WHERE session_id = ? AND viewer_agent_id = ? AND message_id = ?
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (session_id, SHARED_VIEWER_AGENT_ID, message_id),
        )
        if row is None:
            return None
        return _record_to_message(_row_to_record(row))
