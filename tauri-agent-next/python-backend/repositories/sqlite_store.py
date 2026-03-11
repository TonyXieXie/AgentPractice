from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


class SqliteStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    async def execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        commit: bool = False,
    ) -> None:
        await asyncio.to_thread(self._execute_sync, sql, params, commit)

    async def execute_insert(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> int:
        return await asyncio.to_thread(self._execute_insert_sync, sql, params)

    async def fetchone(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> Optional[sqlite3.Row]:
        return await asyncio.to_thread(self._fetchone_sync, sql, params)

    async def fetchall(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._fetchall_sync, sql, params)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        return conn

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")

    def _execute_sync(
        self,
        sql: str,
        params: Sequence[Any],
        commit: bool,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(sql, params)
            if commit:
                conn.commit()
        finally:
            conn.close()

    def _execute_insert_sync(self, sql: str, params: Sequence[Any]) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def _fetchone_sync(self, sql: str, params: Sequence[Any]) -> Optional[sqlite3.Row]:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            return cursor.fetchone()
        finally:
            conn.close()

    def _fetchall_sync(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            return list(cursor.fetchall())
        finally:
            conn.close()

    def _initialize_sync(self) -> None:
        conn = self._connect()
        try:
            for statement in self._schema_statements():
                conn.execute(statement)
            self._migrate_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(conn, "conversation_events", "agent_id", "TEXT NULL")
        self._ensure_column(conn, "prompt_traces", "agent_id", "TEXT NULL")
        self._ensure_column(conn, "message_center_events", "target_profile", "TEXT NULL")
        self._ensure_column(conn, "message_center_events", "message_type", "TEXT NULL")
        self._ensure_column(conn, "message_center_events", "object_type", "TEXT NULL")
        self._ensure_column(conn, "message_center_events", "rpc_phase", "TEXT NULL")
        self._ensure_column(conn, "message_center_events", "target_agent_id", "TEXT NULL")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        decl: str,
    ) -> None:
        try:
            rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
        except sqlite3.Error:
            return
        existing = {str(row[1]) for row in rows if row and len(row) > 1}
        if column in existing:
            return
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl};")
        except sqlite3.Error:
            return

    def _schema_statements(self) -> Iterable[str]:
        yield """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NULL,
            system_prompt TEXT NULL,
            work_path TEXT NULL,
            llm_config_json TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """.strip()

        yield """
        CREATE TABLE IF NOT EXISTS agent_instances (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            profile_id TEXT NULL,
            role TEXT NOT NULL,
            display_name TEXT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield "CREATE INDEX IF NOT EXISTS idx_agent_instances_session_type ON agent_instances(session_id, agent_type, created_at)"
        yield "CREATE INDEX IF NOT EXISTS idx_agent_instances_session ON agent_instances(session_id, id)"

        yield """
        CREATE TABLE IF NOT EXISTS conversation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            run_id TEXT NULL,
            agent_id TEXT NULL,
            kind TEXT NOT NULL,
            content_json TEXT NOT NULL,
            tool_name TEXT NULL,
            tool_call_id TEXT NULL,
            ok INTEGER NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield "CREATE INDEX IF NOT EXISTS idx_conversation_events_session ON conversation_events(session_id, id)"
        yield "CREATE INDEX IF NOT EXISTS idx_conversation_events_agent ON conversation_events(session_id, agent_id, id)"
        yield "CREATE INDEX IF NOT EXISTS idx_conversation_events_tool_call ON conversation_events(session_id, tool_call_id)"

        yield """
        CREATE TABLE IF NOT EXISTS message_center_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            run_id TEXT NULL,
            viewer_agent_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            seq INTEGER NULL,
            kind TEXT NOT NULL,
            delivery TEXT NOT NULL,
            message_type TEXT NULL,
            object_type TEXT NULL,
            rpc_phase TEXT NULL,
            topic TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            target_id TEXT NULL,
            target_agent_id TEXT NULL,
            target_profile TEXT NULL,
            correlation_id TEXT NULL,
            ok INTEGER NULL,
            visibility TEXT NOT NULL,
            level TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield "CREATE INDEX IF NOT EXISTS idx_message_center_viewer ON message_center_events(session_id, viewer_agent_id, id)"
        yield "CREATE INDEX IF NOT EXISTS idx_message_center_message ON message_center_events(session_id, message_id)"
        yield "CREATE INDEX IF NOT EXISTS idx_message_center_correlation ON message_center_events(session_id, correlation_id)"

        yield """
        CREATE TABLE IF NOT EXISTS agent_prompt_state (
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            summary_text TEXT NOT NULL DEFAULT '',
            summarized_until_event_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, agent_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield """
        CREATE TABLE IF NOT EXISTS prompt_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            run_id TEXT NULL,
            agent_id TEXT NULL,
            llm_model TEXT NULL,
            max_context_tokens INTEGER NOT NULL,
            prompt_budget INTEGER NOT NULL,
            estimated_prompt_tokens INTEGER NOT NULL,
            rendered_message_count INTEGER NOT NULL,
            actions_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield "CREATE INDEX IF NOT EXISTS idx_prompt_traces_session ON prompt_traces(session_id, id)"

        yield """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT NULL,
            agent_id TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            source_message_kind TEXT NOT NULL,
            topic TEXT NOT NULL,
            status TEXT NOT NULL,
            work_path TEXT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NULL,
            error_text TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """.strip()

        yield "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, created_at, id)"
        yield "CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(session_id, agent_id, created_at, id)"
        yield "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(run_id, status, updated_at)"
