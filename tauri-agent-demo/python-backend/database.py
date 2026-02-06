from typing import List, Optional, Dict, Any
import json
from datetime import datetime
import sqlite3
import uuid
from models import LLMConfig, LLMConfigCreate, LLMConfigUpdate, ChatMessage, ChatMessageCreate, ChatSession, ChatSessionCreate, ChatSessionUpdate

DATABASE_PATH = "chat_app.db"

class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_steps_message ON agent_steps(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_permission_status ON tool_permission_requests(status)')
        
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
            INSERT INTO chat_sessions (id, title, config_id, work_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, session.title, session.config_id, session.work_path, now, now))
        
        conn.commit()
        conn.close()
        
        return self.get_session(session_id)
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get session"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.*, COUNT(m.id) as message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON s.id = m.session_id
            WHERE s.id = ?
            GROUP BY s.id
        ''', (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return ChatSession(**dict(row))
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
        
        return [ChatSession(**dict(row)) for row in rows]
    
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

        if fields:
            fields.append("updated_at = ?")
            values.append(datetime.now().isoformat())
            values.append(session_id)
            sql = f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(sql, values)
            conn.commit()

        conn.close()
        return self.get_session(session_id)
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session and its messages"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM message_attachments
            WHERE message_id IN (
                SELECT id FROM chat_messages WHERE session_id = ?
            )
        ''', (session_id,))
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

# Create global database instance

db = Database()
