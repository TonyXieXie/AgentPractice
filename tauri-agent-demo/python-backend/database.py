import sqlite3
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
from models import LLMConfig, LLMConfigCreate, LLMConfigUpdate, ChatMessage, ChatMessageCreate, ChatSession, ChatSessionCreate, ChatSessionUpdate

DATABASE_PATH = "chat_app.db"

class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """初始化数据库表"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 创建 LLM 配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS llm_configs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_type TEXT NOT NULL,
                api_key TEXT NOT NULL,
                base_url TEXT,
                model TEXT NOT NULL,
                temperature REAL DEFAULT 0.7,
                max_tokens INTEGER DEFAULT 2000,
                is_default INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        ''')
        
        # 创建会话表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                config_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (config_id) REFERENCES llm_configs(id)
            )
        ''')
        
        # 添加 agent_type 字段迁移
        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN agent_type TEXT DEFAULT "simple"')
        except sqlite3.OperationalError:
            pass  # 字段已存在
        
        # 创建消息表
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
        
        # 创建 Agent 步骤表
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
        
        # 创建工具调用记录表
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
        
        # 添加字段迁移逻辑（如果表已存在但缺少新字段）
        try:
            cursor.execute('ALTER TABLE chat_messages ADD COLUMN raw_request TEXT')
        except sqlite3.OperationalError:
            pass  # 字段已存在
        
        try:
            cursor.execute('ALTER TABLE chat_messages ADD COLUMN raw_response TEXT')
        except sqlite3.OperationalError:
            pass  # 字段已存在
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_config ON chat_sessions(config_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_steps_message ON agent_steps(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls(message_id)')
        
        conn.commit()
        conn.close()
    
    # ==================== LLM 配置管理 ====================
    
    def create_config(self, config: LLMConfigCreate) -> LLMConfig:
        """创建新的 LLM 配置"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        config_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        
        # 如果设置为默认，先取消其他配置的默认状态
        if config.is_default:
            cursor.execute('UPDATE llm_configs SET is_default = 0')
        
        cursor.execute('''
            INSERT INTO llm_configs (id, name, api_type, api_key, base_url, model, temperature, max_tokens, is_default, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (config_id, config.name, config.api_type, config.api_key, config.base_url, 
              config.model, config.temperature, config.max_tokens, int(config.is_default), created_at))
        
        conn.commit()
        conn.close()
        
        return self.get_config(config_id)
    
    def get_config(self, config_id: str) -> Optional[LLMConfig]:
        """获取指定配置"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs WHERE id = ?', (config_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return LLMConfig(
                id=row['id'],
                name=row['name'],
                api_type=row['api_type'],
                api_key=row['api_key'],
                base_url=row['base_url'],
                model=row['model'],
                temperature=row['temperature'],
                max_tokens=row['max_tokens'],
                is_default=bool(row['is_default']),
                created_at=row['created_at']
            )
        return None
    
    def get_all_configs(self) -> List[LLMConfig]:
        """获取所有配置"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs ORDER BY is_default DESC, created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        
        return [LLMConfig(**dict(row)) for row in rows]
    
    def get_default_config(self) -> Optional[LLMConfig]:
        """获取默认配置"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM llm_configs WHERE is_default = 1 LIMIT 1')
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return LLMConfig(**dict(row))
        return None
    
    def update_config(self, config_id: str, update: LLMConfigUpdate) -> Optional[LLMConfig]:
        """更新配置"""
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
        if update.temperature is not None:
            update_fields.append('temperature = ?')
            values.append(update.temperature)
        if update.max_tokens is not None:
            update_fields.append('max_tokens = ?')
            values.append(update.max_tokens)
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
        """删除配置"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM llm_configs WHERE id = ?', (config_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    # ==================== 会话管理 ====================
    
    def create_session(self, session: ChatSessionCreate) -> ChatSession:
        """创建新会话"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO chat_sessions (id, title, config_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, session.title, session.config_id, now, now))
        
        conn.commit()
        conn.close()
        
        return self.get_session(session_id)
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取指定会话"""
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
        """获取所有会话"""
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
        """更新会话"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if update.title is not None:
            cursor.execute('''
                UPDATE chat_sessions 
                SET title = ?, updated_at = ?
                WHERE id = ?
            ''', (update.title, datetime.now().isoformat(), session_id))
            conn.commit()
        
        conn.close()
        return self.get_session(session_id)
    
    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有消息"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM chat_messages WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    # ==================== 消息管理 ====================
    
    def create_message(self, message: ChatMessageCreate) -> ChatMessage:
        """创建新消息"""
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
        
        # 更新会话的 updated_at
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
        """获取会话的消息历史"""
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
        conn.close()
        
        messages = []
        for row in rows:
            metadata = json.loads(row['metadata']) if row['metadata'] else None
            raw_request = json.loads(row['raw_request']) if row['raw_request'] else None
            raw_response = json.loads(row['raw_response']) if row['raw_response'] else None
            messages.append(ChatMessage(
                id=row['id'],
                session_id=row['session_id'],
                role=row['role'],
                content=row['content'],
                timestamp=row['timestamp'],
                metadata=metadata,
                raw_request=raw_request,
                raw_response=raw_response
            ))
        
        if limit:
            messages.reverse()
        
        return messages
    
    # ==================== Agent Steps 和 Tool Calls 管理 ====================
    
    def save_agent_step(self, message_id: int, step_type: str, content: str, sequence: int, metadata: Dict[str, Any] = None) -> int:
        """保存Agent执行步骤"""
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
        """获取消息的所有Agent步骤"""
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
    
    def save_tool_call(self, message_id: int, tool_name: str, tool_input: str, tool_output: str) -> int:
        """保存工具调用记录"""
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
    
    def update_session_agent_type(self, session_id: str, agent_type: str):
        """更新会话的Agent类型"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE chat_sessions
            SET agent_type = ?, updated_at = ?
            WHERE id = ?
        ''', (agent_type, datetime.now().isoformat(), session_id))
        
        conn.commit()
        conn.close()

# 创建全局数据库实例
db = Database()
