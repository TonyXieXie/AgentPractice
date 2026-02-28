from typing import List, Optional, Dict, Any, Set
import json
import os
from datetime import datetime
import sqlite3
import uuid
from models import (
    LLMConfig,
    LLMConfigCreate,
    LLMConfigUpdate,
    ChatMessage,
    ChatMessageCreate,
    ChatSession,
    ChatSessionCreate,
    ChatSessionUpdate,
    AgentInstance,
    AgentTask,
    AgentTaskEvent,
    AgentArtifact,
    TaskStatus,
    TaskErrorCode
)

DATABASE_PATH = os.getenv("TAURI_AGENT_DB_PATH", "chat_app.db")

class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS llm_configs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_type TEXT,
                api_format TEXT,
                api_profile TEXT,
                api_key TEXT NOT NULL,
                base_url TEXT,
                model TEXT NOT NULL,
                temperature REAL DEFAULT 0.7,
                max_tokens INTEGER DEFAULT 2000,
                max_context_tokens INTEGER DEFAULT 200000,
                is_default INTEGER DEFAULT 0,
                reasoning_effort TEXT DEFAULT "medium",
                reasoning_summary TEXT DEFAULT "detailed",
                created_at TEXT NOT NULL
            )
        ''')

        try:
            cursor.execute('ALTER TABLE llm_configs ADD COLUMN api_format TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE llm_configs ADD COLUMN api_profile TEXT')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE llm_configs ADD COLUMN reasoning_effort TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE llm_configs ADD COLUMN reasoning_summary TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE llm_configs ADD COLUMN max_context_tokens INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('''
                UPDATE llm_configs
                SET api_format = ?
                WHERE api_format IS NULL
            ''', ("openai_chat_completions",))
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('''
                UPDATE llm_configs
                SET reasoning_effort = ?
                WHERE reasoning_effort IS NULL
            ''', ("medium",))
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('''
                UPDATE llm_configs
                SET reasoning_summary = ?
                WHERE reasoning_summary IS NULL
            ''', ("detailed",))
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('''
                UPDATE llm_configs
                SET max_context_tokens = ?
                WHERE max_context_tokens IS NULL
            ''', (200000,))
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('''
                UPDATE llm_configs
                SET api_profile = api_type
                WHERE api_profile IS NULL AND api_type IS NOT NULL
            ''')
            cursor.execute('''
                UPDATE llm_configs
                SET api_profile = ?
                WHERE api_profile IS NULL
            ''', ("openai",))
        except sqlite3.OperationalError:
            pass
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                config_id TEXT NOT NULL,
                work_path TEXT,
                agent_profile TEXT,
                parent_session_id TEXT,
                context_summary TEXT,
                last_compressed_llm_call_id INTEGER,
                context_estimate TEXT,
                context_estimate_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (config_id) REFERENCES llm_configs(id)
            )
        ''')

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN agent_type TEXT DEFAULT "simple"')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN work_path TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN agent_profile TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN parent_session_id TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN context_summary TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN last_compressed_llm_call_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN context_estimate TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN context_estimate_at TEXT')
        except sqlite3.OperationalError:
            pass
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT,
                raw_request TEXT,
                raw_response TEXT,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                name TEXT,
                mime TEXT,
                width INTEGER,
                height INTEGER,
                size INTEGER,
                data BLOB NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                step_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_input TEXT,
                tool_output TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                message_id INTEGER,
                agent_type TEXT,
                iteration INTEGER,
                stream INTEGER NOT NULL,
                api_type TEXT,
                api_profile TEXT,
                api_format TEXT,
                model TEXT,
                request_json TEXT,
                response_json TEXT,
                response_text TEXT,
                processed_json TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tool_permission_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                tool_name TEXT NOT NULL,
                action TEXT NOT NULL,
                path TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id INTEGER,
                tree_hash TEXT NOT NULL,
                work_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, message_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS session_tool_call_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id INTEGER,
                agent_type TEXT,
                iteration INTEGER,
                tool_name TEXT NOT NULL,
                success INTEGER NOT NULL,
                failure_reason TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        try:
            cursor.execute('ALTER TABLE tool_permission_requests ADD COLUMN session_id TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE llm_calls ADD COLUMN api_profile TEXT')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE chat_messages ADD COLUMN raw_request TEXT')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE chat_messages ADD COLUMN raw_response TEXT')
        except sqlite3.OperationalError:
            pass
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_attachments_message ON message_attachments(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_config ON chat_sessions(config_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_parent ON chat_sessions(parent_session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_steps_message ON agent_steps(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_permission_status ON tool_permission_requests(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_session ON file_snapshots(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_message ON file_snapshots(session_id, message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_history_session ON session_tool_call_history(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_history_session_tool ON session_tool_call_history(session_id, tool_name)')
        self.migrate_agent_tasks_up(cursor)
        
        conn.commit()
        conn.close()

    def migrate_agent_tasks_up(self, cursor: Optional[sqlite3.Cursor] = None) -> None:
        own_connection = False
        conn: Optional[sqlite3.Connection] = None
        if cursor is None:
            own_connection = True
            conn = self.get_connection()
            cursor = conn.cursor()

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_instances (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                name TEXT,
                abilities TEXT,
                metadata TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, profile_id)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                title TEXT,
                input TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_instance_id TEXT,
                created_by_instance_id TEXT,
                target_profile_id TEXT,
                required_abilities TEXT,
                parent_task_id TEXT,
                root_task_id TEXT,
                source_task_id TEXT,
                loop_group_id TEXT,
                loop_iteration INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 2,
                retry_count INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT,
                error_code TEXT,
                error_message TEXT,
                result TEXT,
                metadata TEXT,
                legacy_child_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(session_id, idempotency_key)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT,
                message TEXT,
                payload TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, seq)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT,
                uri TEXT,
                tree_hash TEXT,
                checksum TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_task_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_task_id TEXT NOT NULL,
                to_task_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(from_task_id, to_task_id, edge_type)
            )
            '''
        )

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_instances_session ON agent_instances(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_instances_profile ON agent_instances(profile_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_tasks_session_status_created ON agent_tasks(session_id, status, created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_tasks_instance_status_created ON agent_tasks(assigned_instance_id, status, created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_tasks_parent ON agent_tasks(parent_task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_tasks_root ON agent_tasks(root_task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_tasks_source ON agent_tasks(source_task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_task_events_task_seq ON agent_task_events(task_id, seq)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_artifacts_task ON agent_artifacts(task_id, created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_edges_from ON agent_task_edges(from_task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_edges_to ON agent_task_edges(to_task_id)')

        if own_connection and conn is not None:
            conn.commit()
            conn.close()

    def migrate_agent_tasks_down(self) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DROP TABLE IF EXISTS agent_task_edges')
        cursor.execute('DROP TABLE IF EXISTS agent_artifacts')
        cursor.execute('DROP TABLE IF EXISTS agent_task_events')
        cursor.execute('DROP TABLE IF EXISTS agent_tasks')
        cursor.execute('DROP TABLE IF EXISTS agent_instances')
        conn.commit()
        conn.close()
    
    # ==================== LLM Configs ====================
    
    def create_config(self, config: LLMConfigCreate) -> LLMConfig:
        """Create LLM config"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        config_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        api_format = config.api_format or "openai_chat_completions"
        api_profile = config.api_profile or config.api_type or "openai"
        reasoning_effort = config.reasoning_effort or "medium"
        reasoning_summary = config.reasoning_summary or "detailed"
        
        if config.is_default:
            cursor.execute('UPDATE llm_configs SET is_default = 0')
        
        cursor.execute('''
            INSERT INTO llm_configs (id, name, api_type, api_format, api_profile, api_key, base_url, model, temperature, max_tokens, max_context_tokens, is_default, reasoning_effort, reasoning_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (config_id, config.name, api_profile, api_format, api_profile, config.api_key, config.base_url,
              config.model, config.temperature, config.max_tokens, config.max_context_tokens, int(config.is_default), reasoning_effort, reasoning_summary, created_at))
        
        conn.commit()
        conn.close()
        
        return self.get_config(config_id)
    
    def get_config(self, config_id: str) -> Optional[LLMConfig]:
        """Get config"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs WHERE id = ?', (config_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            api_profile = row['api_profile'] if 'api_profile' in row.keys() else None
            if not api_profile:
                api_profile = row['api_type'] if 'api_type' in row.keys() else None
            if not api_profile:
                api_profile = "openai"
            reasoning_effort = row['reasoning_effort'] if 'reasoning_effort' in row.keys() else None
            if not reasoning_effort:
                reasoning_effort = "medium"
            reasoning_summary = row['reasoning_summary'] if 'reasoning_summary' in row.keys() else None
            if not reasoning_summary:
                reasoning_summary = "detailed"
            return LLMConfig(
                id=row['id'],
                name=row['name'],
                api_format=row['api_format'] or "openai_chat_completions",
                api_profile=api_profile,
                api_key=row['api_key'],
                base_url=row['base_url'],
                model=row['model'],
                temperature=row['temperature'],
                max_tokens=row['max_tokens'],
                max_context_tokens=row['max_context_tokens'] if 'max_context_tokens' in row.keys() else 200000,
                is_default=bool(row['is_default']),
                reasoning_effort=reasoning_effort,
                reasoning_summary=reasoning_summary,
                created_at=row['created_at']
            )
        return None
    
    def get_all_configs(self) -> List[LLMConfig]:
        """Get all configs"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs ORDER BY is_default DESC, created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        
        configs = []
        for row in rows:
            data = dict(row)
            data["api_profile"] = data.get("api_profile") or data.get("api_type") or "openai"
            data["api_format"] = data.get("api_format") or "openai_chat_completions"
            data["reasoning_effort"] = data.get("reasoning_effort") or "medium"
            data["reasoning_summary"] = data.get("reasoning_summary") or "detailed"
            data["max_context_tokens"] = data.get("max_context_tokens") or 200000
            configs.append(LLMConfig(**data))
        return configs
    
    def get_default_config(self) -> Optional[LLMConfig]:
        """Get default config"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs WHERE is_default = 1 LIMIT 1')
        row = cursor.fetchone()
        conn.close()
        
        if row:
            data = dict(row)
            data["api_profile"] = data.get("api_profile") or data.get("api_type") or "openai"
            data["api_format"] = data.get("api_format") or "openai_chat_completions"
            data["reasoning_effort"] = data.get("reasoning_effort") or "medium"
            data["reasoning_summary"] = data.get("reasoning_summary") or "detailed"
            data["max_context_tokens"] = data.get("max_context_tokens") or 200000
            return LLMConfig(**data)
        return None
    
    def update_config(self, config_id: str, update: LLMConfigUpdate) -> Optional[LLMConfig]:
        """Update config"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        update_fields = []
        values = []
        
        if update.name is not None:
            update_fields.append('name = ?')
            values.append(update.name)
        if update.api_key is not None:
            update_fields.append('api_key = ?')
            values.append(update.api_key)
        if update.base_url is not None:
            update_fields.append('base_url = ?')
            values.append(update.base_url)
        if update.model is not None:
            update_fields.append('model = ?')
            values.append(update.model)
        if update.api_format is not None:
            update_fields.append('api_format = ?')
            values.append(update.api_format)
        if update.api_profile is not None:
            update_fields.append('api_profile = ?')
            values.append(update.api_profile)
            update_fields.append('api_type = ?')
            values.append(update.api_profile)
        elif update.api_type is not None:
            update_fields.append('api_profile = ?')
            values.append(update.api_type)
            update_fields.append('api_type = ?')
            values.append(update.api_type)
        if update.temperature is not None:
            update_fields.append('temperature = ?')
            values.append(update.temperature)
        if update.max_tokens is not None:
            update_fields.append('max_tokens = ?')
            values.append(update.max_tokens)
        if update.max_context_tokens is not None:
            update_fields.append('max_context_tokens = ?')
            values.append(update.max_context_tokens)
        if update.reasoning_effort is not None:
            update_fields.append('reasoning_effort = ?')
            values.append(update.reasoning_effort)
        if update.reasoning_summary is not None:
            update_fields.append('reasoning_summary = ?')
            values.append(update.reasoning_summary)
        if update.is_default is not None:
            if update.is_default:
                cursor.execute('UPDATE llm_configs SET is_default = 0')
            update_fields.append('is_default = ?')
            values.append(int(update.is_default))
        
        if not update_fields:
            conn.close()
            return self.get_config(config_id)
        
        values.append(config_id)
        sql = f"UPDATE llm_configs SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(sql, values)
        conn.commit()
        conn.close()
        
        return self.get_config(config_id)
    
    def delete_config(self, config_id: str) -> bool:
        """Delete config"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM llm_configs WHERE id = ?', (config_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    # ==================== Sessions ====================
    
    def create_session(self, session: ChatSessionCreate) -> ChatSession:
        """Create session"""
        conn = self.get_connection()
        cursor = conn.cursor()

        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        cursor.execute('''
            INSERT INTO chat_sessions (id, title, config_id, work_path, agent_profile, parent_session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session_id, session.title, session.config_id, session.work_path, session.agent_profile, session.parent_session_id, now, now))
        
        conn.commit()
        conn.close()
        
        return self.get_session(session_id)
    
    def get_session(self, session_id: str, include_count: bool = True) -> Optional[ChatSession]:
        """Get session"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if include_count:
            cursor.execute('''
                SELECT s.*, COUNT(m.id) as message_count
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON s.id = m.session_id
                WHERE s.id = ?
                GROUP BY s.id
            ''', (session_id,))
        else:
            cursor.execute('SELECT * FROM chat_sessions WHERE id = ?', (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            data = dict(row)
            estimate_raw = data.get("context_estimate")
            if estimate_raw:
                try:
                    data["context_estimate"] = json.loads(estimate_raw)
                except Exception:
                    data["context_estimate"] = None
            return ChatSession(**data)
        return None
    
    def get_all_sessions(self) -> List[ChatSession]:
        """Get all sessions"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.*, COUNT(m.id) as message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        sessions: List[ChatSession] = []
        for row in rows:
            data = dict(row)
            estimate_raw = data.get("context_estimate")
            if estimate_raw:
                try:
                    data["context_estimate"] = json.loads(estimate_raw)
                except Exception:
                    data["context_estimate"] = None
            sessions.append(ChatSession(**data))
        return sessions
    
    def update_session(self, session_id: str, update: ChatSessionUpdate) -> Optional[ChatSession]:
        """Update session"""
        conn = self.get_connection()
        cursor = conn.cursor()
        fields = []
        values = []

        if update.title is not None:
            fields.append("title = ?")
            values.append(update.title)

        if update.work_path is not None:
            fields.append("work_path = ?")
            values.append(update.work_path)

        if update.config_id is not None:
            fields.append("config_id = ?")
            values.append(update.config_id)

        if update.agent_profile is not None:
            fields.append("agent_profile = ?")
            values.append(update.agent_profile)

        if update.parent_session_id is not None:
            fields.append("parent_session_id = ?")
            values.append(update.parent_session_id)

        if fields:
            fields.append("updated_at = ?")
            values.append(datetime.now().isoformat())
            values.append(session_id)
            sql = f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(sql, values)
            conn.commit()

        conn.close()
        return self.get_session(session_id)

    def copy_session(self, session_id: str, title: Optional[str] = None) -> Optional[ChatSession]:
        """Clone a session and its related records."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT * FROM chat_sessions WHERE id = ?', (session_id,))
            source = cursor.fetchone()
            if not source:
                return None

            new_session_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            new_title = title or source['title']

            cursor.execute(
                '''
                INSERT INTO chat_sessions (id, title, config_id, work_path, agent_profile, parent_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    new_session_id,
                    new_title,
                    source['config_id'],
                    source['work_path'],
                    source['agent_profile'],
                    None,
                    now,
                    now,
                )
            )

            cursor.execute(
                '''
                SELECT id, role, content, timestamp, metadata, raw_request, raw_response
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id ASC
                ''',
                (session_id,)
            )
            messages = cursor.fetchall()
            message_id_map: Dict[int, int] = {}
            for row in messages:
                cursor.execute(
                    '''
                    INSERT INTO chat_messages (session_id, role, content, timestamp, metadata, raw_request, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        new_session_id,
                        row['role'],
                        row['content'],
                        row['timestamp'],
                        row['metadata'],
                        row['raw_request'],
                        row['raw_response'],
                    )
                )
                message_id_map[row['id']] = cursor.lastrowid

            if message_id_map:
                placeholders = ",".join(["?"] * len(message_id_map))
                message_ids = list(message_id_map.keys())

                cursor.execute(
                    f'''
                    SELECT message_id, name, mime, width, height, size, data, created_at
                    FROM message_attachments
                    WHERE message_id IN ({placeholders})
                    ORDER BY created_at ASC
                    ''',
                    message_ids
                )
                for row in cursor.fetchall():
                    data = row['data']
                    if isinstance(data, memoryview):
                        data = data.tobytes()
                    cursor.execute(
                        '''
                        INSERT INTO message_attachments (message_id, name, mime, width, height, size, data, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            message_id_map[row['message_id']],
                            row['name'],
                            row['mime'],
                            row['width'],
                            row['height'],
                            row['size'],
                            data,
                            row['created_at'],
                        )
                    )

                cursor.execute(
                    f'''
                    SELECT message_id, step_type, content, metadata, sequence, timestamp
                    FROM agent_steps
                    WHERE message_id IN ({placeholders})
                    ORDER BY message_id ASC, sequence ASC
                    ''',
                    message_ids
                )
                for row in cursor.fetchall():
                    cursor.execute(
                        '''
                        INSERT INTO agent_steps (message_id, step_type, content, metadata, sequence, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            message_id_map[row['message_id']],
                            row['step_type'],
                            row['content'],
                            row['metadata'],
                            row['sequence'],
                            row['timestamp'],
                        )
                    )

                cursor.execute(
                    f'''
                    SELECT message_id, tool_name, tool_input, tool_output, timestamp
                    FROM tool_calls
                    WHERE message_id IN ({placeholders})
                    ORDER BY message_id ASC
                    ''',
                    message_ids
                )
                for row in cursor.fetchall():
                    cursor.execute(
                        '''
                        INSERT INTO tool_calls (message_id, tool_name, tool_input, tool_output, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                        ''',
                        (
                            message_id_map[row['message_id']],
                            row['tool_name'],
                            row['tool_input'],
                            row['tool_output'],
                            row['timestamp'],
                        )
                    )

                cursor.execute(
                    '''
                    SELECT session_id, message_id, tree_hash, work_path, created_at
                    FROM file_snapshots
                    WHERE session_id = ?
                    ORDER BY id ASC
                    ''',
                    (session_id,)
                )
                for row in cursor.fetchall():
                    old_message_id = row['message_id']
                    new_message_id = message_id_map.get(old_message_id) if old_message_id is not None else None
                    cursor.execute(
                        '''
                        INSERT INTO file_snapshots (session_id, message_id, tree_hash, work_path, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ''',
                        (
                            new_session_id,
                            new_message_id,
                            row['tree_hash'],
                            row['work_path'],
                            row['created_at'],
                        )
                    )

            cursor.execute(
                '''
                SELECT message_id, agent_type, iteration, stream, api_type, api_profile, api_format, model,
                       request_json, response_json, response_text, processed_json, created_at
                FROM llm_calls
                WHERE session_id = ?
                ORDER BY id ASC
                ''',
                (session_id,)
            )
            for row in cursor.fetchall():
                old_message_id = row['message_id']
                new_message_id = message_id_map.get(old_message_id) if old_message_id is not None else None
                cursor.execute(
                    '''
                    INSERT INTO llm_calls (
                        session_id, message_id, agent_type, iteration, stream, api_type, api_profile, api_format, model,
                        request_json, response_json, response_text, processed_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        new_session_id,
                        new_message_id,
                        row['agent_type'],
                        row['iteration'],
                        row['stream'],
                        row['api_type'],
                        row['api_profile'],
                        row['api_format'],
                        row['model'],
                        row['request_json'],
                        row['response_json'],
                        row['response_text'],
                        row['processed_json'],
                        row['created_at'],
                    )
                )

            cursor.execute(
                '''
                SELECT message_id, agent_type, iteration, tool_name, success, failure_reason, created_at
                FROM session_tool_call_history
                WHERE session_id = ?
                ORDER BY id ASC
                ''',
                (session_id,)
            )
            for row in cursor.fetchall():
                old_message_id = row['message_id']
                new_message_id = message_id_map.get(old_message_id) if old_message_id is not None else None
                cursor.execute(
                    '''
                    INSERT INTO session_tool_call_history (
                        session_id, message_id, agent_type, iteration, tool_name, success, failure_reason, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        new_session_id,
                        new_message_id,
                        row['agent_type'],
                        row['iteration'],
                        row['tool_name'],
                        row['success'],
                        row['failure_reason'],
                        row['created_at'],
                    )
                )

            conn.commit()
            return self.get_session(new_session_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_session_context(
        self,
        session_id: str,
        summary: Optional[str],
        last_call_id: Optional[int]
    ) -> Optional[ChatSession]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE chat_sessions
            SET context_summary = ?, last_compressed_llm_call_id = ?, updated_at = ?
            WHERE id = ?
        ''', (summary, last_call_id, datetime.now().isoformat(), session_id))
        conn.commit()
        conn.close()
        return self.get_session(session_id)

    def update_session_context_estimate(
        self,
        session_id: str,
        estimate: Optional[Dict[str, Any]]
    ) -> Optional[ChatSession]:
        conn = self.get_connection()
        cursor = conn.cursor()
        payload = json.dumps(estimate) if estimate else None
        now = datetime.now().isoformat()
        cursor.execute('''
            UPDATE chat_sessions
            SET context_estimate = ?, context_estimate_at = ?, updated_at = ?
            WHERE id = ?
        ''', (payload, now if estimate else None, now, session_id))
        conn.commit()
        conn.close()
        return self.get_session(session_id)
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session and its messages"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM chat_sessions WHERE parent_session_id = ?', (session_id,))
        child_rows = cursor.fetchall()
        conn.close()
        for row in child_rows:
            try:
                child_id = row["id"]
            except Exception:
                child_id = row[0] if row else None
            if child_id:
                self.delete_session(str(child_id))

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM message_attachments
            WHERE message_id IN (
                SELECT id FROM chat_messages WHERE session_id = ?
            )
        ''', (session_id,))
        cursor.execute('DELETE FROM session_tool_call_history WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM chat_messages WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    # ==================== Messages ====================
    
    def create_message(self, message: ChatMessageCreate) -> ChatMessage:
        """Create message"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        metadata_json = json.dumps(message.metadata) if message.metadata else None
        raw_request_json = json.dumps(message.raw_request) if message.raw_request else None
        raw_response_json = json.dumps(message.raw_response) if message.raw_response else None
        
        cursor.execute('''
            INSERT INTO chat_messages (session_id, role, content, timestamp, metadata, raw_request, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (message.session_id, message.role, message.content, timestamp, metadata_json, raw_request_json, raw_response_json))
        
        message_id = cursor.lastrowid
        
        cursor.execute('''
            UPDATE chat_sessions SET updated_at = ? WHERE id = ?
        ''', (timestamp, message.session_id))
        
        conn.commit()
        conn.close()
        
        return ChatMessage(
            id=message_id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            timestamp=timestamp,
            metadata=message.metadata,
            raw_request=message.raw_request,
            raw_response=message.raw_response
        )
    
    def get_session_messages(self, session_id: str, limit: Optional[int] = None) -> List[ChatMessage]:
        """Get session messages"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if limit:
            cursor.execute('''
                SELECT * FROM chat_messages 
                WHERE session_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (session_id, limit))
        else:
            cursor.execute('''
                SELECT * FROM chat_messages 
                WHERE session_id = ? 
                ORDER BY timestamp ASC
            ''', (session_id,))
        
        rows = cursor.fetchall()
        message_ids = [row['id'] for row in rows]
        attachments_by_message: Dict[int, List[Dict[str, Any]]] = {}
        if message_ids:
            placeholders = ",".join(["?"] * len(message_ids))
            cursor.execute(
                f'''
                SELECT id, message_id, name, mime, width, height, size, created_at
                FROM message_attachments
                WHERE message_id IN ({placeholders})
                ORDER BY created_at ASC
                ''',
                message_ids
            )
            for attach_row in cursor.fetchall():
                data = dict(attach_row)
                msg_id = data.get("message_id")
                if msg_id is None:
                    continue
                attachments_by_message.setdefault(msg_id, []).append(data)

        conn.close()

        messages = []
        for row in rows:
            metadata = json.loads(row['metadata']) if row['metadata'] else None
            raw_request = json.loads(row['raw_request']) if row['raw_request'] else None
            raw_response = json.loads(row['raw_response']) if row['raw_response'] else None
            attachments = attachments_by_message.get(row['id'])
            messages.append(ChatMessage(
                id=row['id'],
                session_id=row['session_id'],
                role=row['role'],
                content=row['content'],
                timestamp=row['timestamp'],
                metadata=metadata,
                raw_request=raw_request,
                raw_response=raw_response,
                attachments=attachments
            ))
        
        if limit:
            messages.reverse()

        return messages

    def get_session_messages_before(self, session_id: str, before_id: int, limit: int) -> List[ChatMessage]:
        """Get session messages before a message id (latest first, then reversed)."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM chat_messages
            WHERE session_id = ? AND id < ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (session_id, before_id, limit))

        rows = cursor.fetchall()
        message_ids = [row['id'] for row in rows]
        attachments_by_message: Dict[int, List[Dict[str, Any]]] = {}
        if message_ids:
            placeholders = ",".join(["?"] * len(message_ids))
            cursor.execute(
                f'''
                SELECT id, message_id, name, mime, width, height, size, created_at
                FROM message_attachments
                WHERE message_id IN ({placeholders})
                ORDER BY created_at ASC
                ''',
                message_ids
            )
            for attach_row in cursor.fetchall():
                data = dict(attach_row)
                msg_id = data.get("message_id")
                if msg_id is None:
                    continue
                attachments_by_message.setdefault(msg_id, []).append(data)

        conn.close()

        messages = []
        for row in rows:
            metadata = json.loads(row['metadata']) if row['metadata'] else None
            raw_request = json.loads(row['raw_request']) if row['raw_request'] else None
            raw_response = json.loads(row['raw_response']) if row['raw_response'] else None
            attachments = attachments_by_message.get(row['id'])
            messages.append(ChatMessage(
                id=row['id'],
                session_id=row['session_id'],
                role=row['role'],
                content=row['content'],
                timestamp=row['timestamp'],
                metadata=metadata,
                raw_request=raw_request,
                raw_response=raw_response,
                attachments=attachments
            ))

        messages.reverse()
        return messages

    def get_message(self, session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, session_id, role, content, timestamp
            FROM chat_messages
            WHERE id = ? AND session_id = ?
        ''', (message_id, session_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_previous_user_message_id(self, session_id: str, before_message_id: int) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id
            FROM chat_messages
            WHERE session_id = ? AND role = 'user' AND id < ?
            ORDER BY id DESC
            LIMIT 1
        ''', (session_id, before_message_id))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return int(row["id"])

    def get_dialogue_messages_between(
        self,
        session_id: str,
        start_id: int,
        end_id: int
    ) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, role, content, timestamp
            FROM chat_messages
            WHERE session_id = ? AND id >= ? AND id <= ? AND role IN ('user', 'assistant')
            ORDER BY id ASC
        ''', (session_id, start_id, end_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_dialogue_messages_after(
        self,
        session_id: str,
        after_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if after_id is None:
            cursor.execute('''
                SELECT id, role, content, timestamp
                FROM chat_messages
                WHERE session_id = ? AND role IN ('user', 'assistant')
                ORDER BY id ASC
            ''', (session_id,))
        else:
            cursor.execute('''
                SELECT id, role, content, timestamp
                FROM chat_messages
                WHERE session_id = ? AND id > ? AND role IN ('user', 'assistant')
                ORDER BY id ASC
            ''', (session_id, after_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_latest_assistant_message_id(self, session_id: str) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id
            FROM chat_messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY id DESC
            LIMIT 1
        ''', (session_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return int(row["id"])
        except (TypeError, ValueError):
            return None

    def save_message_attachment(
        self,
        message_id: int,
        name: Optional[str],
        mime: Optional[str],
        data: bytes,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size: Optional[int] = None
    ) -> Dict[str, Any]:
        conn = self.get_connection()
        cursor = conn.cursor()
        created_at = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO message_attachments (message_id, name, mime, width, height, size, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (message_id, name, mime, width, height, size, data, created_at))
        attachment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {
            "id": attachment_id,
            "message_id": message_id,
            "name": name,
            "mime": mime,
            "width": width,
            "height": height,
            "size": size,
            "created_at": created_at
        }

    def save_message_attachments(self, message_id: int, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        saved: List[Dict[str, Any]] = []
        for item in attachments:
            saved.append(
                self.save_message_attachment(
                    message_id=message_id,
                    name=item.get("name"),
                    mime=item.get("mime"),
                    data=item.get("data") or b"",
                    width=item.get("width"),
                    height=item.get("height"),
                    size=item.get("size")
                )
            )
        return saved

    def get_message_attachments(self, message_id: int) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, message_id, name, mime, width, height, size, created_at
            FROM message_attachments
            WHERE message_id = ?
            ORDER BY created_at ASC
        ''', (message_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_attachment(self, attachment_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT *
            FROM message_attachments
            WHERE id = ?
        ''', (attachment_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    # ==================== Agent Steps + Tool Calls ====================
    
    def save_agent_step(self, message_id: int, step_type: str, content: str, sequence: int, metadata: Dict[str, Any] = None) -> int:
        """Save agent step"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        metadata_json = json.dumps(metadata) if metadata else None
        
        cursor.execute('''
            INSERT INTO agent_steps (message_id, step_type, content, metadata, sequence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (message_id, step_type, content, metadata_json, sequence, timestamp))
        
        step_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return step_id
    
    def get_agent_steps(self, message_id: int) -> List[Dict[str, Any]]:
        """Get agent steps for message"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, step_type, content, metadata, sequence, timestamp
            FROM agent_steps
            WHERE message_id = ?
            ORDER BY sequence ASC
        ''', (message_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "id": row["id"],
                "step_type": row["step_type"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "sequence": row["sequence"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]

    def get_session_agent_steps(self, session_id: str) -> List[Dict[str, Any]]:
        """Get agent steps for all messages in a session"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.message_id, s.step_type, s.content, s.metadata, s.sequence, s.timestamp
            FROM agent_steps s
            JOIN chat_messages m ON s.message_id = m.id
            WHERE m.session_id = ?
            ORDER BY s.message_id ASC, s.sequence ASC
        ''', (session_id,))
        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "message_id": row["message_id"],
                "step_type": row["step_type"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "sequence": row["sequence"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]

    def get_session_agent_steps_for_messages(self, session_id: str, message_ids: List[int]) -> List[Dict[str, Any]]:
        """Get agent steps for specific messages in a session."""
        if not message_ids:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        placeholders = ",".join(["?"] * len(message_ids))
        cursor.execute(
            f'''
            SELECT s.message_id, s.step_type, s.content, s.metadata, s.sequence, s.timestamp
            FROM agent_steps s
            JOIN chat_messages m ON s.message_id = m.id
            WHERE m.session_id = ? AND s.message_id IN ({placeholders})
            ORDER BY s.message_id ASC, s.sequence ASC
            ''',
            [session_id, *message_ids]
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "message_id": row["message_id"],
                "step_type": row["step_type"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "sequence": row["sequence"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]

    def get_spawned_subagent_child_sessions(
        self,
        session_id: str,
        message_id: Optional[int] = None,
        min_message_id: Optional[int] = None
    ) -> List[str]:
        """Extract spawned subagent child session ids from agent steps."""
        if not session_id:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        params: List[Any] = [session_id]
        where = "m.session_id = ? AND s.step_type = 'observation'"
        if message_id is not None:
            where += " AND m.id = ?"
            params.append(message_id)
        elif min_message_id is not None:
            where += " AND m.id >= ?"
            params.append(min_message_id)
        cursor.execute(
            f'''
            SELECT s.content, s.metadata
            FROM agent_steps s
            JOIN chat_messages m ON s.message_id = m.id
            WHERE {where}
            ORDER BY s.message_id ASC, s.sequence ASC
            ''',
            params
        )
        rows = cursor.fetchall()
        conn.close()

        results: List[str] = []
        seen: Set[str] = set()
        for row in rows:
            metadata = {}
            try:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            except Exception:
                metadata = {}
            tool_name = str(metadata.get("tool") or "")
            if tool_name.lower() != "spawn_subagent":
                continue
            payload = None
            content = row["content"]
            if content:
                try:
                    payload = json.loads(content)
                except Exception:
                    payload = None
            if isinstance(payload, dict):
                child_id = payload.get("child_session_id")
                if child_id:
                    child_id = str(child_id)
                    if child_id and child_id not in seen:
                        seen.add(child_id)
                        results.append(child_id)
        return results
    
    def save_tool_call(self, message_id: int, tool_name: str, tool_input: str, tool_output: str) -> int:
        """Save tool call record"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO tool_calls (message_id, tool_name, tool_input, tool_output, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (message_id, tool_name, tool_input, tool_output, timestamp))
        
        tool_call_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return tool_call_id

    # ==================== Tool Permission Requests ====================

    def create_permission_request(self, tool_name: str, action: str, path: str, reason: str = None, session_id: str = None) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO tool_permission_requests (tool_name, action, path, reason, status, created_at, updated_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tool_name, action, path, reason, "pending", timestamp, timestamp, session_id))
        request_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return request_id

    def get_permission_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM tool_permission_requests WHERE id = ?
        ''', (request_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_permission_requests(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if status:
            cursor.execute('''
                SELECT * FROM tool_permission_requests
                WHERE status = ?
                ORDER BY created_at DESC
            ''', (status,))
        else:
            cursor.execute('''
                SELECT * FROM tool_permission_requests
                ORDER BY created_at DESC
            ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update_permission_request(self, request_id: int, status: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tool_permission_requests
            SET status = ?, updated_at = ?
            WHERE id = ?
        ''', (status, datetime.now().isoformat(), request_id))
        conn.commit()
        cursor.execute('SELECT * FROM tool_permission_requests WHERE id = ?', (request_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    # ==================== File Snapshots ====================

    def get_file_snapshot(self, session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT *
            FROM file_snapshots
            WHERE session_id = ? AND message_id = ?
            ORDER BY created_at ASC
            LIMIT 1
        ''', (session_id, message_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def create_file_snapshot(self, session_id: str, message_id: int, tree_hash: str, work_path: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        created_at = datetime.now().isoformat()
        try:
            cursor.execute('''
                INSERT INTO file_snapshots (session_id, message_id, tree_hash, work_path, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, message_id, tree_hash, work_path, created_at))
            snapshot_id = cursor.lastrowid
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return self.get_file_snapshot(session_id, message_id)

        cursor.execute('''
            SELECT *
            FROM file_snapshots
            WHERE id = ?
        ''', (snapshot_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_snapshot_for_rollback(self, session_id: str, user_message_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id
            FROM chat_messages
            WHERE session_id = ? AND id > ? AND role = 'assistant'
            ORDER BY id ASC
            LIMIT 1
        ''', (session_id, user_message_id))
        msg_row = cursor.fetchone()
        if not msg_row:
            conn.close()
            return None

        assistant_id = msg_row["id"]
        cursor.execute('''
            SELECT *
            FROM file_snapshots
            WHERE session_id = ? AND message_id = ?
            ORDER BY created_at ASC
            LIMIT 1
        ''', (session_id, assistant_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def delete_file_snapshots_from(self, session_id: str, message_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM file_snapshots
            WHERE session_id = ? AND message_id >= ?
        ''', (session_id, message_id))
        conn.commit()
        conn.close()

    # ==================== LLM Calls Debug ====================

    def save_llm_call(
        self,
        session_id: str,
        message_id: int,
        agent_type: str,
        iteration: int,
        stream: bool,
        api_profile: str,
        api_format: str,
        model: str,
        request_json: Dict[str, Any],
        response_json: Dict[str, Any],
        response_text: str,
        processed_json: Optional[Dict[str, Any]] = None
    ) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO llm_calls (
                session_id, message_id, agent_type, iteration, stream,
                api_type, api_profile, api_format, model, request_json, response_json,
                response_text, processed_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            message_id,
            agent_type,
            iteration,
            int(bool(stream)),
            api_profile,
            api_profile,
            api_format,
            model,
            json.dumps(request_json) if request_json is not None else None,
            json.dumps(response_json) if response_json is not None else None,
            response_text,
            json.dumps(processed_json) if processed_json is not None else None,
            timestamp
        ))
        llm_call_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return llm_call_id

    def update_llm_call_processed(self, llm_call_id: int, processed_json: Dict[str, Any]):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE llm_calls
            SET processed_json = ?
            WHERE id = ?
        ''', (json.dumps(processed_json) if processed_json is not None else None, llm_call_id))
        conn.commit()
        conn.close()

    def get_session_llm_calls(self, session_id: str) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM llm_calls
            WHERE session_id = ?
            ORDER BY created_at ASC
        ''', (session_id,))
        rows = cursor.fetchall()
        conn.close()
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "agent_type": row["agent_type"],
                "iteration": row["iteration"],
                "stream": bool(row["stream"]),
                "api_type": row["api_type"],
                "api_profile": row["api_profile"] if "api_profile" in row.keys() else None,
                "api_format": row["api_format"],
                "model": row["model"],
                "request_json": json.loads(row["request_json"]) if row["request_json"] else None,
                "response_json": json.loads(row["response_json"]) if row["response_json"] else None,
                "response_text": row["response_text"],
                "processed_json": json.loads(row["processed_json"]) if row["processed_json"] else None,
                "created_at": row["created_at"],
            })
        return results

    def save_session_tool_call_history(
        self,
        session_id: str,
        tool_name: str,
        success: bool,
        message_id: Optional[int] = None,
        agent_type: Optional[str] = None,
        iteration: Optional[int] = None,
        failure_reason: Optional[str] = None
    ) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO session_tool_call_history (
                session_id, message_id, agent_type, iteration, tool_name, success, failure_reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            message_id,
            agent_type,
            iteration,
            tool_name,
            1 if success else 0,
            failure_reason,
            timestamp
        ))
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def get_session_tool_stats(self, session_id: str) -> Dict[str, Any]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT
                COUNT(*) AS total_calls,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_calls
            FROM session_tool_call_history
            WHERE session_id = ?
        ''', (session_id,))
        total_row = cursor.fetchone()

        total_calls = int(total_row["total_calls"]) if total_row and total_row["total_calls"] is not None else 0
        success_calls = int(total_row["success_calls"]) if total_row and total_row["success_calls"] is not None else 0
        failed_calls = max(0, total_calls - success_calls)
        success_rate = (success_calls / total_calls) if total_calls > 0 else 0.0

        cursor.execute('''
            SELECT
                tool_name,
                COUNT(*) AS total_calls,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_calls
            FROM session_tool_call_history
            WHERE session_id = ?
            GROUP BY tool_name
            ORDER BY total_calls DESC, tool_name ASC
        ''', (session_id,))
        rows = cursor.fetchall()
        conn.close()

        tools: List[Dict[str, Any]] = []
        for row in rows:
            item_total = int(row["total_calls"]) if row["total_calls"] is not None else 0
            item_success = int(row["success_calls"]) if row["success_calls"] is not None else 0
            item_failed = max(0, item_total - item_success)
            item_rate = (item_success / item_total) if item_total > 0 else 0.0
            tools.append({
                "tool_name": row["tool_name"],
                "total_calls": item_total,
                "success_calls": item_success,
                "failed_calls": item_failed,
                "success_rate": item_rate,
            })

        return {
            "session_id": session_id,
            "total_calls": total_calls,
            "success_calls": success_calls,
            "failed_calls": failed_calls,
            "success_rate": success_rate,
            "tools": tools
        }

    def get_llm_call_metas_after(self, session_id: str, after_id: int = 0) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, message_id
            FROM llm_calls
            WHERE session_id = ? AND id > ?
            ORDER BY id ASC
        ''', (session_id, after_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_max_message_id_for_llm_call(self, session_id: str, call_id: int) -> Optional[int]:
        if not call_id:
            return None
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(message_id) as message_id
            FROM llm_calls
            WHERE session_id = ? AND id <= ? AND message_id IS NOT NULL
        ''', (session_id, call_id))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        value = row["message_id"]
        return int(value) if value is not None else None

    def get_latest_llm_call_id(self, session_id: str) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(id) as id
            FROM llm_calls
            WHERE session_id = ?
        ''', (session_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        value = row["id"]
        return int(value) if value is not None else None

    def update_session_agent_type(self, session_id: str, agent_type: str):
        """Update session agent type"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE chat_sessions
            SET agent_type = ?, updated_at = ?
            WHERE id = ?
        ''', (agent_type, datetime.now().isoformat(), session_id))
        
        conn.commit()
        conn.close()

    # ==================== Rollback ====================

    def rollback_session(self, session_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, role, content
            FROM chat_messages
            WHERE id = ? AND session_id = ?
        ''', (message_id, session_id))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        if row["role"] != "user":
            conn.close()
            return {"error": "Rollback target must be a user message."}

        input_message = row["content"]
        timestamp = datetime.now().isoformat()

        cursor.execute('''
            DELETE FROM agent_steps
            WHERE message_id IN (
                SELECT id FROM chat_messages
                WHERE session_id = ? AND id >= ?
            )
        ''', (session_id, message_id))

        cursor.execute('''
            DELETE FROM tool_calls
            WHERE message_id IN (
                SELECT id FROM chat_messages
                WHERE session_id = ? AND id >= ?
            )
        ''', (session_id, message_id))

        cursor.execute('''
            DELETE FROM llm_calls
            WHERE session_id = ? AND (message_id IS NULL OR message_id >= ?)
        ''', (session_id, message_id))

        cursor.execute('''
            DELETE FROM message_attachments
            WHERE message_id IN (
                SELECT id FROM chat_messages
                WHERE session_id = ? AND id >= ?
            )
        ''', (session_id, message_id))

        cursor.execute('''
            DELETE FROM chat_messages
            WHERE session_id = ? AND id >= ?
        ''', (session_id, message_id))

        cursor.execute('''
            UPDATE chat_sessions
            SET updated_at = ?
            WHERE id = ?
        ''', (timestamp, session_id))

        cursor.execute('''
            SELECT COUNT(*) as count
            FROM chat_messages
            WHERE session_id = ?
        ''', (session_id,))
        count_row = cursor.fetchone()
        remaining = count_row["count"] if count_row else 0

        conn.commit()
        conn.close()

        return {
            "session_id": session_id,
            "input_message": input_message,
            "remaining_messages": remaining
        }

    # ==================== Agent Instances ====================

    def _parse_json_field(self, value: Optional[str], fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except Exception:
            return fallback

    def upsert_agent_instance(
        self,
        session_id: str,
        profile_id: str,
        name: Optional[str] = None,
        abilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        instance_id: Optional[str] = None,
        status: str = "active"
    ) -> AgentInstance:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            '''
            SELECT id FROM agent_instances
            WHERE session_id = ? AND profile_id = ?
            LIMIT 1
            ''',
            (session_id, profile_id)
        )
        row = cursor.fetchone()
        if row:
            existing_id = row["id"]
            cursor.execute(
                '''
                UPDATE agent_instances
                SET name = ?, abilities = ?, metadata = ?, status = ?, updated_at = ?
                WHERE id = ?
                ''',
                (
                    name,
                    json.dumps(abilities or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    status,
                    now,
                    existing_id
                )
            )
            result_id = existing_id
        else:
            result_id = instance_id or str(uuid.uuid4())
            cursor.execute(
                '''
                INSERT INTO agent_instances (id, session_id, profile_id, name, abilities, metadata, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    result_id,
                    session_id,
                    profile_id,
                    name,
                    json.dumps(abilities or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    status,
                    now,
                    now
                )
            )
        conn.commit()
        conn.close()
        instance = self.get_agent_instance(result_id)
        if not instance:
            raise RuntimeError("Failed to upsert agent instance")
        return instance

    def get_agent_instance(self, instance_id: str) -> Optional[AgentInstance]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM agent_instances
            WHERE id = ?
            LIMIT 1
            ''',
            (instance_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        data = dict(row)
        return AgentInstance(
            id=data["id"],
            session_id=data["session_id"],
            profile_id=data["profile_id"],
            name=data.get("name"),
            abilities=self._parse_json_field(data.get("abilities"), []),
            metadata=self._parse_json_field(data.get("metadata"), {}),
            status=data.get("status") or "active",
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at")
        )

    def list_agent_instances(
        self,
        session_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[AgentInstance]:
        conn = self.get_connection()
        cursor = conn.cursor()
        where: List[str] = []
        values: List[Any] = []
        if session_id:
            where.append("session_id = ?")
            values.append(session_id)
        if profile_id:
            where.append("profile_id = ?")
            values.append(profile_id)
        if status:
            where.append("status = ?")
            values.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cursor.execute(
            f'''
            SELECT * FROM agent_instances
            {where_sql}
            ORDER BY created_at ASC
            ''',
            values
        )
        rows = cursor.fetchall()
        conn.close()
        results: List[AgentInstance] = []
        for row in rows:
            data = dict(row)
            results.append(
                AgentInstance(
                    id=data["id"],
                    session_id=data["session_id"],
                    profile_id=data["profile_id"],
                    name=data.get("name"),
                    abilities=self._parse_json_field(data.get("abilities"), []),
                    metadata=self._parse_json_field(data.get("metadata"), {}),
                    status=data.get("status") or "active",
                    created_at=data.get("created_at"),
                    updated_at=data.get("updated_at")
                )
            )
        return results

    # ==================== Agent Tasks ====================

    def _task_from_row(self, row: sqlite3.Row) -> AgentTask:
        data = dict(row)
        return AgentTask(
            id=data["id"],
            session_id=data["session_id"],
            title=data.get("title"),
            input=data.get("input") or "",
            status=TaskStatus(data.get("status") or TaskStatus.pending.value),
            assigned_instance_id=data.get("assigned_instance_id"),
            created_by_instance_id=data.get("created_by_instance_id"),
            target_profile_id=data.get("target_profile_id"),
            required_abilities=self._parse_json_field(data.get("required_abilities"), []),
            parent_task_id=data.get("parent_task_id"),
            root_task_id=data.get("root_task_id"),
            source_task_id=data.get("source_task_id"),
            loop_group_id=data.get("loop_group_id"),
            loop_iteration=int(data.get("loop_iteration") or 0),
            max_retries=int(data.get("max_retries") or 0),
            retry_count=int(data.get("retry_count") or 0),
            idempotency_key=data.get("idempotency_key"),
            error_code=TaskErrorCode(data["error_code"]) if data.get("error_code") else None,
            error_message=data.get("error_message"),
            result=data.get("result"),
            metadata=self._parse_json_field(data.get("metadata"), {}),
            legacy_child_session_id=data.get("legacy_child_session_id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at")
        )

    def _task_event_from_row(self, row: sqlite3.Row) -> AgentTaskEvent:
        data = dict(row)
        return AgentTaskEvent(
            id=data.get("id"),
            task_id=data["task_id"],
            seq=int(data["seq"]),
            event_type=data["event_type"],
            status=TaskStatus(data["status"]) if data.get("status") else None,
            message=data.get("message"),
            payload=self._parse_json_field(data.get("payload"), {}),
            error_code=TaskErrorCode(data["error_code"]) if data.get("error_code") else None,
            error_message=data.get("error_message"),
            created_at=data.get("created_at")
        )

    def _artifact_from_row(self, row: sqlite3.Row) -> AgentArtifact:
        data = dict(row)
        return AgentArtifact(
            id=data.get("id"),
            task_id=data["task_id"],
            session_id=data["session_id"],
            artifact_type=data["artifact_type"],
            path=data.get("path"),
            uri=data.get("uri"),
            tree_hash=data.get("tree_hash"),
            checksum=data.get("checksum"),
            metadata=self._parse_json_field(data.get("metadata"), {}),
            created_at=data.get("created_at")
        )

    def get_agent_task_by_idempotency(self, session_id: str, idempotency_key: str) -> Optional[AgentTask]:
        if not session_id or not idempotency_key:
            return None
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT *
            FROM agent_tasks
            WHERE session_id = ? AND idempotency_key = ?
            LIMIT 1
            ''',
            (session_id, idempotency_key)
        )
        row = cursor.fetchone()
        conn.close()
        return self._task_from_row(row) if row else None

    def create_agent_task(
        self,
        *,
        session_id: str,
        title: Optional[str],
        input_text: str,
        status: TaskStatus,
        assigned_instance_id: Optional[str] = None,
        created_by_instance_id: Optional[str] = None,
        target_profile_id: Optional[str] = None,
        required_abilities: Optional[List[str]] = None,
        parent_task_id: Optional[str] = None,
        root_task_id: Optional[str] = None,
        source_task_id: Optional[str] = None,
        loop_group_id: Optional[str] = None,
        loop_iteration: int = 0,
        max_retries: int = 2,
        retry_count: int = 0,
        idempotency_key: Optional[str] = None,
        error_code: Optional[TaskErrorCode] = None,
        error_message: Optional[str] = None,
        result: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        legacy_child_session_id: Optional[str] = None,
        initial_event: Optional[Dict[str, Any]] = None
    ) -> AgentTask:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        task_id = str(uuid.uuid4())
        cursor.execute(
            '''
            INSERT INTO agent_tasks (
                id, session_id, title, input, status, assigned_instance_id, created_by_instance_id, target_profile_id,
                required_abilities, parent_task_id, root_task_id, source_task_id, loop_group_id, loop_iteration,
                max_retries, retry_count, idempotency_key, error_code, error_message, result, metadata, legacy_child_session_id,
                created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                task_id,
                session_id,
                title,
                input_text,
                status.value if isinstance(status, TaskStatus) else str(status),
                assigned_instance_id,
                created_by_instance_id,
                target_profile_id,
                json.dumps(required_abilities or [], ensure_ascii=False),
                parent_task_id,
                root_task_id,
                source_task_id,
                loop_group_id,
                int(loop_iteration or 0),
                int(max_retries or 0),
                int(retry_count or 0),
                idempotency_key,
                error_code.value if isinstance(error_code, TaskErrorCode) else error_code,
                error_message,
                result,
                json.dumps(metadata or {}, ensure_ascii=False),
                legacy_child_session_id,
                now,
                now,
                now if status == TaskStatus.running else None,
                now if status in (TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled) else None
            )
        )
        if initial_event:
            self.append_agent_task_event(
                task_id=task_id,
                event_type=str(initial_event.get("event_type") or "task_progress"),
                status=initial_event.get("status") or status,
                message=initial_event.get("message"),
                payload=initial_event.get("payload") or {},
                error_code=initial_event.get("error_code"),
                error_message=initial_event.get("error_message"),
                conn=conn
            )
        conn.commit()
        conn.close()
        task = self.get_agent_task(task_id)
        if not task:
            raise RuntimeError("Failed to create agent task")
        return task

    def get_agent_task(self, task_id: str) -> Optional[AgentTask]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT *
            FROM agent_tasks
            WHERE id = ?
            LIMIT 1
            ''',
            (task_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return self._task_from_row(row) if row else None

    def list_agent_tasks(
        self,
        *,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
        instance_id: Optional[str] = None,
        limit: int = 200
    ) -> List[AgentTask]:
        conn = self.get_connection()
        cursor = conn.cursor()
        where: List[str] = []
        values: List[Any] = []
        if session_id:
            where.append("session_id = ?")
            values.append(session_id)
        if status:
            where.append("status = ?")
            values.append(status)
        if instance_id:
            where.append("assigned_instance_id = ?")
            values.append(instance_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        values.append(int(limit))
        cursor.execute(
            f'''
            SELECT *
            FROM agent_tasks
            {where_sql}
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            ''',
            values
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._task_from_row(row) for row in rows]

    def update_agent_task(
        self,
        task_id: str,
        **fields: Any
    ) -> Optional[AgentTask]:
        if not fields:
            return self.get_agent_task(task_id)

        serialized: Dict[str, Any] = {}
        for key, value in fields.items():
            if key in ("required_abilities", "metadata") and value is not None:
                serialized[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, TaskStatus):
                serialized[key] = value.value
            elif isinstance(value, TaskErrorCode):
                serialized[key] = value.value
            else:
                serialized[key] = value

        if "status" in serialized:
            status = serialized["status"]
            now = datetime.now().isoformat()
            if status == TaskStatus.running.value:
                serialized.setdefault("started_at", now)
            if status in (TaskStatus.succeeded.value, TaskStatus.failed.value, TaskStatus.cancelled.value):
                serialized.setdefault("finished_at", now)

        serialized["updated_at"] = datetime.now().isoformat()
        assignments = ", ".join([f"{key} = ?" for key in serialized.keys()])
        values = list(serialized.values()) + [task_id]
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE agent_tasks SET {assignments} WHERE id = ?", values)
        conn.commit()
        conn.close()
        return self.get_agent_task(task_id)

    def append_agent_task_event(
        self,
        *,
        task_id: str,
        event_type: str,
        status: Optional[Any] = None,
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        error_code: Optional[Any] = None,
        error_message: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None
    ) -> AgentTaskEvent:
        own_connection = conn is None
        if own_connection:
            conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT COALESCE(MAX(seq), 0) AS max_seq
            FROM agent_task_events
            WHERE task_id = ?
            ''',
            (task_id,)
        )
        row = cursor.fetchone()
        next_seq = int((row["max_seq"] if row and row["max_seq"] is not None else 0) or 0) + 1
        now = datetime.now().isoformat()
        normalized_status = status.value if isinstance(status, TaskStatus) else status
        normalized_code = error_code.value if isinstance(error_code, TaskErrorCode) else error_code
        cursor.execute(
            '''
            INSERT INTO agent_task_events (
                task_id, seq, event_type, status, message, payload, error_code, error_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                task_id,
                next_seq,
                event_type,
                normalized_status,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
                normalized_code,
                error_message,
                now
            )
        )
        event_id = cursor.lastrowid
        if own_connection:
            conn.commit()
            conn.close()
        return AgentTaskEvent(
            id=event_id,
            task_id=task_id,
            seq=next_seq,
            event_type=event_type,
            status=TaskStatus(normalized_status) if normalized_status else None,
            message=message,
            payload=payload or {},
            error_code=TaskErrorCode(normalized_code) if normalized_code else None,
            error_message=error_message,
            created_at=now
        )

    def list_agent_task_events(
        self,
        task_id: str,
        after_seq: int = 0,
        limit: int = 500
    ) -> List[AgentTaskEvent]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT *
            FROM agent_task_events
            WHERE task_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            ''',
            (task_id, int(after_seq or 0), int(limit))
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._task_event_from_row(row) for row in rows]

    def add_agent_task_edge(
        self,
        from_task_id: str,
        to_task_id: str,
        edge_type: str = "handoff",
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR IGNORE INTO agent_task_edges (from_task_id, to_task_id, edge_type, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                from_task_id,
                to_task_id,
                edge_type,
                json.dumps(metadata or {}, ensure_ascii=False),
                datetime.now().isoformat()
            )
        )
        conn.commit()
        conn.close()

    def list_downstream_task_ids(self, task_id: str) -> List[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT to_task_id
            FROM agent_task_edges
            WHERE from_task_id = ?
            ORDER BY id ASC
            ''',
            (task_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [str(row["to_task_id"]) for row in rows if row["to_task_id"]]

    def save_agent_artifact(
        self,
        *,
        task_id: str,
        session_id: str,
        artifact_type: str,
        path: Optional[str] = None,
        uri: Optional[str] = None,
        tree_hash: Optional[str] = None,
        checksum: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentArtifact:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            '''
            INSERT INTO agent_artifacts (
                task_id, session_id, artifact_type, path, uri, tree_hash, checksum, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                task_id,
                session_id,
                artifact_type,
                path,
                uri,
                tree_hash,
                checksum,
                json.dumps(metadata or {}, ensure_ascii=False),
                now
            )
        )
        artifact_id = cursor.lastrowid
        conn.commit()
        cursor.execute('SELECT * FROM agent_artifacts WHERE id = ?', (artifact_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            raise RuntimeError("Failed to save agent artifact")
        return self._artifact_from_row(row)

    def list_agent_artifacts(self, task_id: str) -> List[AgentArtifact]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT *
            FROM agent_artifacts
            WHERE task_id = ?
            ORDER BY created_at ASC
            ''',
            (task_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._artifact_from_row(row) for row in rows]

    def get_latest_file_snapshot_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT *
            FROM file_snapshots
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

# Create global database instance

db = Database()
