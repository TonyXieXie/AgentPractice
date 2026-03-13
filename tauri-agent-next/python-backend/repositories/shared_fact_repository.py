from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from agents.message import AgentMessage, utc_now_iso
from observation.facts import SharedFact
from repositories.sqlite_store import SqliteStore


def _load_json_dict(raw: object) -> Dict[str, Any]:
    text = str(raw or "")
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_fact(row) -> SharedFact:
    return SharedFact(
        fact_id=str(row["fact_id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        fact_seq=int(row["fact_seq"]),
        message_id=row["message_id"],
        sender_id=str(row["sender_id"]),
        target_agent_id=row["target_agent_id"],
        target_profile_id=row["target_profile_id"],
        topic=str(row["topic"]),
        fact_type=str(row["fact_type"]),
        payload_json=_load_json_dict(row["payload_json"]),
        metadata_json=_load_json_dict(row["metadata_json"]),
        visibility=str(row["visibility"]),
        level=str(row["level"]),
        created_at=str(row["created_at"]),
    )


class SharedFactRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append(
        self,
        *,
        session_id: str,
        sender_id: str,
        topic: str,
        fact_type: str,
        payload_json: Dict[str, Any],
        metadata_json: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        target_profile_id: Optional[str] = None,
        visibility: str = "public",
        level: str = "info",
        fact_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> SharedFact:
        resolved_fact_id = str(fact_id or "").strip() or SharedFact(
            session_id=session_id,
            sender_id=sender_id,
            topic=topic,
            fact_type=fact_type,
        ).fact_id
        resolved_created_at = created_at or utc_now_iso()
        fact_seq = await self.store.execute_insert(
            """
            INSERT INTO shared_facts
              (fact_id, session_id, run_id, message_id, sender_id, target_agent_id, target_profile_id,
               topic, fact_type, payload_json, metadata_json, visibility, level, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                resolved_fact_id,
                session_id,
                run_id,
                message_id,
                sender_id,
                target_agent_id,
                target_profile_id,
                topic,
                fact_type,
                json.dumps(payload_json or {}, ensure_ascii=False),
                json.dumps(metadata_json or {}, ensure_ascii=False),
                visibility,
                level,
                resolved_created_at,
            ),
        )
        if fact_seq <= 0:
            raise RuntimeError("failed to insert shared_fact")
        return SharedFact(
            fact_id=resolved_fact_id,
            session_id=session_id,
            run_id=run_id,
            fact_seq=int(fact_seq),
            message_id=message_id,
            sender_id=sender_id,
            target_agent_id=target_agent_id,
            target_profile_id=target_profile_id,
            topic=topic,
            fact_type=fact_type,
            payload_json=dict(payload_json or {}),
            metadata_json=dict(metadata_json or {}),
            visibility=visibility,
            level=level,
            created_at=resolved_created_at,
        )

    async def list(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
        run_id: Optional[str] = None,
        sender_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        target_profile_id: Optional[str] = None,
        fact_type: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> list[SharedFact]:
        clauses = ["session_id = ?", "fact_seq > ?"]
        params: list[Any] = [session_id, int(after_seq)]
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if sender_id is not None:
            clauses.append("sender_id = ?")
            params.append(sender_id)
        if target_agent_id is not None:
            clauses.append("target_agent_id = ?")
            params.append(target_agent_id)
        if target_profile_id is not None:
            clauses.append("target_profile_id = ?")
            params.append(target_profile_id)
        if fact_type is not None:
            clauses.append("fact_type = ?")
            params.append(fact_type)
        if topic is not None:
            clauses.append("topic = ?")
            params.append(topic)
        params.append(int(limit))
        rows = await self.store.fetchall(
            f"""
            SELECT fact_id, session_id, run_id, fact_seq, message_id, sender_id, target_agent_id,
                   target_profile_id, topic, fact_type, payload_json, metadata_json, visibility,
                   level, created_at
            FROM shared_facts
            WHERE {' AND '.join(clauses)}
            ORDER BY fact_seq ASC
            LIMIT ?
            """.strip(),
            tuple(params),
        )
        return [_row_to_fact(row) for row in rows]

    async def get_by_message_id(
        self,
        session_id: str,
        message_id: str,
    ) -> Optional[SharedFact]:
        row = await self.store.fetchone(
            """
            SELECT fact_id, session_id, run_id, fact_seq, message_id, sender_id, target_agent_id,
                   target_profile_id, topic, fact_type, payload_json, metadata_json, visibility,
                   level, created_at
            FROM shared_facts
            WHERE session_id = ? AND message_id = ?
            ORDER BY fact_seq DESC
            LIMIT 1
            """.strip(),
            (session_id, message_id),
        )
        if row is None:
            return None
        return _row_to_fact(row)

    async def get_latest_for_run(self, run_id: str) -> Optional[SharedFact]:
        row = await self.store.fetchone(
            """
            SELECT fact_id, session_id, run_id, fact_seq, message_id, sender_id, target_agent_id,
                   target_profile_id, topic, fact_type, payload_json, metadata_json, visibility,
                   level, created_at
            FROM shared_facts
            WHERE run_id = ?
            ORDER BY fact_seq DESC
            LIMIT 1
            """.strip(),
            (run_id,),
        )
        if row is None:
            return None
        return _row_to_fact(row)

    async def get_latest_run_status_fact(self, run_id: str) -> Optional[SharedFact]:
        row = await self.store.fetchone(
            """
            SELECT fact_id, session_id, run_id, fact_seq, message_id, sender_id, target_agent_id,
                   target_profile_id, topic, fact_type, payload_json, metadata_json, visibility,
                   level, created_at
            FROM shared_facts
            WHERE run_id = ? AND fact_type = 'run_lifecycle'
            ORDER BY fact_seq DESC
            LIMIT 1
            """.strip(),
            (run_id,),
        )
        if row is None:
            return None
        return _row_to_fact(row)

    def to_agent_message(self, fact: SharedFact) -> AgentMessage:
        metadata = dict(fact.metadata)
        message_type = str(metadata.get("message_type") or "event")
        object_type = str(metadata.get("object_type") or "target")
        rpc_phase = metadata.get("rpc_phase")
        correlation_id = metadata.get("correlation_id")
        ok = metadata.get("ok")
        target = None
        if object_type != "broadcast" and (fact.target_agent_id or fact.target_profile_id):
            target = AgentMessage.TargetRef(
                agent_id=fact.target_agent_id,
                profile_id=(None if fact.target_agent_id else fact.target_profile_id),
            )
        return AgentMessage(
            id=str(fact.message_id or fact.fact_id),
            message_type=message_type,
            object_type=object_type,
            rpc_phase=rpc_phase,
            topic=fact.topic,
            sender_id=fact.sender_id,
            target=target,
            correlation_id=correlation_id,
            run_id=fact.run_id,
            session_id=fact.session_id,
            seq=fact.fact_seq,
            visibility=fact.visibility,
            level=fact.level,
            ok=ok if isinstance(ok, bool) or ok is None else bool(ok),
            payload=dict(fact.payload),
            metadata=metadata,
            created_at=fact.created_at,
        )
