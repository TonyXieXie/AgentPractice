from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from agents.message import utc_now_iso
from repositories.sqlite_store import SqliteStore


TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped"})


@dataclass(slots=True)
class TaskRecord:
    id: str
    session_id: str
    run_id: Optional[str]
    agent_id: str
    source_message_id: str
    source_message_kind: str
    topic: str
    status: str
    work_path: Optional[str]
    metadata: Dict[str, Any]
    result: Optional[Dict[str, Any]]
    error_text: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str]


def _load_json_dict(raw: object) -> Dict[str, Any]:
    text = str(raw or "")
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_record(row) -> TaskRecord:
    result_text = str(row["result_json"] or "")
    result: Optional[Dict[str, Any]]
    if not result_text:
        result = None
    else:
        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            parsed = None
        result = parsed if isinstance(parsed, dict) else None

    return TaskRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        agent_id=str(row["agent_id"]),
        source_message_id=str(row["source_message_id"]),
        source_message_kind=str(row["source_message_kind"]),
        topic=str(row["topic"]),
        status=str(row["status"]),
        work_path=row["work_path"],
        metadata=_load_json_dict(row["metadata_json"]),
        result=result,
        error_text=row["error_text"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=row["completed_at"],
    )


class TaskRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def create(
        self,
        *,
        session_id: str,
        run_id: Optional[str],
        agent_id: str,
        source_message_id: str,
        source_message_kind: str,
        topic: str,
        status: str,
        work_path: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        task_id = uuid4().hex
        now = utc_now_iso()
        await self.store.execute(
            """
            INSERT INTO tasks
              (id, session_id, run_id, agent_id, source_message_id, source_message_kind, topic,
               status, work_path, metadata_json, result_json, error_text, created_at, updated_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.strip(),
            (
                task_id,
                session_id,
                run_id,
                agent_id,
                source_message_id,
                source_message_kind,
                topic,
                status,
                work_path,
                json.dumps(metadata or {}, ensure_ascii=False),
                None,
                None,
                now,
                now,
                now if status in TERMINAL_TASK_STATUSES else None,
            ),
            commit=True,
        )
        created = await self.get(task_id)
        if created is None:
            raise RuntimeError("failed to create task record")
        return created

    async def get(self, task_id: str) -> TaskRecord | None:
        row = await self.store.fetchone(
            """
            SELECT id, session_id, run_id, agent_id, source_message_id, source_message_kind, topic,
                   status, work_path, metadata_json, result_json, error_text, created_at, updated_at, completed_at
            FROM tasks
            WHERE id = ?
            """.strip(),
            (task_id,),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def list_by_run(
        self,
        run_id: str,
        *,
        statuses: Optional[Iterable[str]] = None,
    ) -> list[TaskRecord]:
        if statuses:
            normalized = [str(item) for item in statuses if str(item).strip()]
            if normalized:
                placeholders = ", ".join("?" for _ in normalized)
                rows = await self.store.fetchall(
                    f"""
                    SELECT id, session_id, run_id, agent_id, source_message_id, source_message_kind, topic,
                           status, work_path, metadata_json, result_json, error_text, created_at, updated_at, completed_at
                    FROM tasks
                    WHERE run_id = ? AND status IN ({placeholders})
                    ORDER BY created_at ASC, id ASC
                    """.strip(),
                    (run_id, *normalized),
                )
                return [_row_to_record(row) for row in rows]
        rows = await self.store.fetchall(
            """
            SELECT id, session_id, run_id, agent_id, source_message_id, source_message_kind, topic,
                   status, work_path, metadata_json, result_json, error_text, created_at, updated_at, completed_at
            FROM tasks
            WHERE run_id = ?
            ORDER BY created_at ASC, id ASC
            """.strip(),
            (run_id,),
        )
        return [_row_to_record(row) for row in rows]

    async def finalize(
        self,
        task_id: str,
        *,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_text: Optional[str] = None,
    ) -> TaskRecord | None:
        current = await self.get(task_id)
        if current is None:
            return None
        if current.status in TERMINAL_TASK_STATUSES:
            return current
        now = utc_now_iso()
        await self.store.execute(
            """
            UPDATE tasks
            SET status = ?,
                result_json = ?,
                error_text = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ? AND status NOT IN ('completed', 'failed', 'stopped')
            """.strip(),
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error_text,
                now,
                now if status in TERMINAL_TASK_STATUSES else None,
                task_id,
            ),
            commit=True,
        )
        return await self.get(task_id)

    async def mark_stopped_by_run(
        self,
        run_id: str,
        *,
        error_text: str = "task stopped by run manager",
    ) -> int:
        records = await self.list_by_run(run_id, statuses=("queued", "running"))
        for record in records:
            await self.finalize(
                record.id,
                status="stopped",
                error_text=error_text,
            )
        return len(records)
