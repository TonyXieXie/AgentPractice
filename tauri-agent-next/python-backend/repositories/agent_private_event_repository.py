from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agents.message import utc_now_iso
from observation.facts import PrivateExecutionEvent
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


def _row_to_private_event(row) -> PrivateExecutionEvent:
    return PrivateExecutionEvent(
        private_event_id=int(row["private_event_id"]),
        session_id=str(row["session_id"]),
        owner_agent_id=str(row["owner_agent_id"]),
        run_id=row["run_id"],
        task_id=row["task_id"],
        message_id=row["message_id"],
        tool_call_id=row["tool_call_id"],
        trigger_fact_id=row["trigger_fact_id"],
        parent_private_event_id=(
            None
            if row["parent_private_event_id"] is None
            else int(row["parent_private_event_id"])
        ),
        kind=str(row["kind"]),
        payload_json=_load_json_dict(row["payload_json"]),
        created_at=str(row["created_at"]),
    )


class AgentPrivateEventRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def append(
        self,
        *,
        session_id: str,
        owner_agent_id: str,
        kind: str,
        payload_json: Dict[str, Any],
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        trigger_fact_id: Optional[str] = None,
        parent_private_event_id: Optional[int] = None,
        created_at: Optional[str] = None,
    ) -> PrivateExecutionEvent:
        resolved_created_at = created_at or utc_now_iso()
        private_event_id = await self.store.execute_insert(
            """
            INSERT INTO agent_private_events
              (session_id, owner_agent_id, run_id, task_id, message_id, tool_call_id,
               trigger_fact_id, parent_private_event_id, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                session_id,
                owner_agent_id,
                run_id,
                task_id,
                message_id,
                tool_call_id,
                trigger_fact_id,
                parent_private_event_id,
                kind,
                json.dumps(payload_json or {}, ensure_ascii=False),
                resolved_created_at,
            ),
        )
        if private_event_id <= 0:
            raise RuntimeError("failed to insert agent_private_event")
        return PrivateExecutionEvent(
            private_event_id=int(private_event_id),
            session_id=session_id,
            owner_agent_id=owner_agent_id,
            run_id=run_id,
            task_id=task_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            trigger_fact_id=trigger_fact_id,
            parent_private_event_id=parent_private_event_id,
            kind=kind,
            payload_json=dict(payload_json or {}),
            created_at=resolved_created_at,
        )

    async def list(
        self,
        session_id: str,
        *,
        owner_agent_id: Optional[str] = None,
        after_id: int = 0,
        limit: int = 100,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        trigger_fact_id: Optional[str] = None,
        kind: Optional[str] = None,
        exclude_kinds: Optional[list[str]] = None,
    ) -> list[PrivateExecutionEvent]:
        clauses = ["session_id = ?", "private_event_id > ?"]
        params: list[Any] = [session_id, int(after_id)]
        if owner_agent_id is not None:
            clauses.append("owner_agent_id = ?")
            params.append(owner_agent_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if message_id is not None:
            clauses.append("message_id = ?")
            params.append(message_id)
        if tool_call_id is not None:
            clauses.append("tool_call_id = ?")
            params.append(tool_call_id)
        if trigger_fact_id is not None:
            clauses.append("trigger_fact_id = ?")
            params.append(trigger_fact_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if exclude_kinds:
            placeholders = ", ".join("?" for _ in exclude_kinds)
            clauses.append(f"kind NOT IN ({placeholders})")
            params.extend(exclude_kinds)
        params.append(int(limit))
        rows = await self.store.fetchall(
            f"""
            SELECT private_event_id, session_id, owner_agent_id, run_id, task_id, message_id,
                   tool_call_id, trigger_fact_id, parent_private_event_id, kind, payload_json,
                   created_at
            FROM agent_private_events
            WHERE {' AND '.join(clauses)}
            ORDER BY private_event_id ASC
            LIMIT ?
            """.strip(),
            tuple(params),
        )
        return [_row_to_private_event(row) for row in rows]

    async def get(self, private_event_id: int) -> Optional[PrivateExecutionEvent]:
        row = await self.store.fetchone(
            """
            SELECT private_event_id, session_id, owner_agent_id, run_id, task_id, message_id,
                   tool_call_id, trigger_fact_id, parent_private_event_id, kind, payload_json,
                   created_at
            FROM agent_private_events
            WHERE private_event_id = ?
            """.strip(),
            (int(private_event_id),),
        )
        if row is None:
            return None
        return _row_to_private_event(row)

    async def get_latest_summary_event(
        self,
        session_id: str,
        owner_agent_id: str,
    ) -> Optional[PrivateExecutionEvent]:
        row = await self.store.fetchone(
            """
            SELECT private_event_id, session_id, owner_agent_id, run_id, task_id, message_id,
                   tool_call_id, trigger_fact_id, parent_private_event_id, kind, payload_json,
                   created_at
            FROM agent_private_events
            WHERE session_id = ? AND owner_agent_id = ? AND kind = 'private_summary'
            ORDER BY private_event_id DESC
            LIMIT 1
            """.strip(),
            (session_id, owner_agent_id),
        )
        if row is None:
            return None
        return _row_to_private_event(row)
