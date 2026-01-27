from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from typing import List, Optional
from datetime import datetime

from models import (
    LLMConfig, LLMConfigCreate, LLMConfigUpdate,
    ChatMessage, ChatMessageCreate,
    ChatSession, ChatSessionCreate, ChatSessionUpdate,
    ChatRequest, ChatResponse, ExportRequest
)
from database import db
from llm_client import create_llm_client
from message_processor import message_processor

app = FastAPI(title="Tauri Agent Chat Backend")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 基础路由 ====================

@app.get("/")
def read_root():
    return {"status": "FastAPI is running!", "version": "2.0"}

# ==================== LLM 配置管理 ====================

@app.get("/configs", response_model=List[LLMConfig])
def get_configs():
    """获取所有 LLM 配置"""
    return db.get_all_configs()

@app.get("/configs/default", response_model=LLMConfig)
def get_default_config():
    """获取默认配置"""
    config = db.get_default_config()
    if not config:
        # 如果没有默认配置，返回第一个配置
        configs = db.get_all_configs()
        if configs:
            return configs[0]
        raise HTTPException(status_code=404, detail="没有可用的配置")
    return config

@app.get("/configs/{config_id}", response_model=LLMConfig)
def get_config(config_id: str):
    """获取指定配置"""
    config = db.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return config

@app.post("/configs", response_model=LLMConfig)
def create_config(config: LLMConfigCreate):
    """创建新配置"""
    return db.create_config(config)

@app.put("/configs/{config_id}", response_model=LLMConfig)
def update_config(config_id: str, update: LLMConfigUpdate):
    """更新配置"""
    config = db.update_config(config_id, update)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return config

@app.delete("/configs/{config_id}")
def delete_config(config_id: str):
    """删除配置"""
    # 检查是否有会话使用该配置
    sessions = db.get_all_sessions()
    if any(s.config_id == config_id for s in sessions):
        raise HTTPException(status_code=400, detail="该配置正在被会话使用，无法删除")
    
    if db.delete_config(config_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="配置不存在")

# ==================== 会话管理 ====================

@app.get("/sessions", response_model=List[ChatSession])
def get_sessions():
    """获取所有会话"""
    return db.get_all_sessions()

@app.get("/sessions/{session_id}", response_model=ChatSession)
def get_session(session_id: str):
    """获取指定会话"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session

@app.post("/sessions", response_model=ChatSession)
def create_session(session: ChatSessionCreate):
    """创建新会话"""
    # 验证配置是否存在
    config = db.get_config(session.config_id)
    if not config:
        raise HTTPException(status_code=404, detail="指定的配置不存在")
    return db.create_session(session)

@app.put("/sessions/{session_id}", response_model=ChatSession)
def update_session(session_id: str, update: ChatSessionUpdate):
    """更新会话"""
    session = db.update_session(session_id, update)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话"""
    if db.delete_session(session_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="会话不存在")

@app.get("/sessions/{session_id}/messages", response_model=List[ChatMessage])
def get_session_messages(session_id: str, limit: Optional[int] = None):
    """获取会话的消息历史"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return db.get_session_messages(session_id, limit)

# ==================== 聊天功能 ====================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    发送聊天消息
    
    流程：
    1. 获取或创建会话
    2. 获取配置
    3. 预处理用户消息
    4. 获取历史消息
    5. 调用 LLM API
    6. 后处理响应
    7. 保存消息
    8. 返回结果
    """
    try:
        # 1. 处理会话
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
        else:
            # 创建新会话
            config_id = request.config_id
            if not config_id:
                default_config = db.get_default_config()
                if not default_config:
                    configs = db.get_all_configs()
                    if not configs:
                        raise HTTPException(status_code=400, detail="没有可用的配置，请先创建配置")
                    config_id = configs[0].id
                else:
                    config_id = default_config.id
            
            session = db.create_session(ChatSessionCreate(
                title="新对话",
                config_id=config_id
            ))
        
        # 2. 获取配置
        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="配置不存在")
        
        # 3. 预处理用户消息
        processed_message = message_processor.preprocess_user_message(request.message)
        
        # 4. 保存用户消息
        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))
        
        # 5. 获取历史消息并构建 LLM 请求
        history = db.get_session_messages(session.id, limit=20)
        # 转换为 LLM API 格式
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history[:-1]  # 排除刚刚添加的用户消息
        ]
        
        # 构建发送给 LLM 的消息
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="你是一个有帮助的AI助手。"
        )
        
        # 构建完整的请求数据（用于debug）
        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "api_type": config.api_type
        }
        
        # 6. 调用 LLM API
        llm_client = create_llm_client(config)
        llm_result = await llm_client.chat(llm_messages)
        
        # 提取内容和原始响应
        llm_response = llm_result["content"]
        raw_response_data = llm_result["raw_response"]
        
        # 7. 后处理响应
        processed_response = message_processor.postprocess_llm_response(llm_response)
        
        # 8. 保存助手消息（包含原始数据）
        assistant_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="assistant",
            content=processed_response,
            raw_request=raw_request_data,
            raw_response=raw_response_data
        ))
        
        # 9. 自动更新会话标题（如果是第一条消息）
        if session.message_count == 0:
            # 使用用户第一条消息的前20个字符作为标题
            title = processed_message[:20] + ("..." if len(processed_message) > 20 else "")
            db.update_session(session.id, ChatSessionUpdate(title=title))
        
        return ChatResponse(
            reply=processed_response,
            session_id=session.id,
            message_id=assistant_msg.id
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"聊天错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理消息时出错: {str(e)}")

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式聊天接口 - 使用SSE逐个返回生成的文本片段"""
    try:
        # 1. 处理会话
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
        else:
            config_id = request.config_id if request.config_id else db.get_all_configs()[0].id
            session = db.create_session(ChatSessionCreate(
                title="新对话",
                config_id=config_id
            ))
        
        # 2. 获取配置
        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="配置不存在")
        
        # 3. 预处理并保存用户消息
        processed_message = message_processor.preprocess_user_message(request.message)
        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))
        
        # 4. 获取历史并构建消息
        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history[:-1]
        ]
        
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="你是一个有帮助的AI助手。"
        )
        
        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "api_type": config.api_type
        }
        
        # 5. 流式生成函数
        async def generate():
            full_response = ""
            
            try:
                llm_client = create_llm_client(config)
                
                async for chunk in llm_client.chat_stream(llm_messages):
                    full_response += chunk
                    # 修复：使用真实换行符，不是转义字符
                    yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                
                processed_response = message_processor.postprocess_llm_response(full_response)
                
                # 流式结束后保存助手消息
                assistant_msg = db.create_message(ChatMessageCreate(
                    session_id=session.id,
                    role="assistant",
                    content=processed_response,
                    raw_request=raw_request_data,
                    raw_response={
                        "content": processed_response,
                        "model": config.model,
                        "finish_reason": "stop"
                    }
                ))
                
                yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg.id})}\n\n"
                
            except Exception as e:
                if full_response:
                    db.create_message(ChatMessageCreate(
                        session_id=session.id,
                        role="assistant",
                        content=full_response + "\n\n[流式中断]",
                        metadata={"error": str(e), "partial": True}
                    ))
                
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 导出功能 ====================

@app.post("/export")
def export_chat_history(request: ExportRequest):
    """导出聊天历史"""
    try:
        if request.session_id:
            # 导出单个会话
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            sessions = [session]
        else:
            # 导出所有会话
            sessions = db.get_all_sessions()
        
        export_data = []
        for session in sessions:
            messages = db.get_session_messages(session.id)
            config = db.get_config(session.config_id)
            
            session_data = {
                "session": {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at,
                    "config": {
                        "name": config.name if config else "未知",
                        "model": config.model if config else "未知"
                    }
                },
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp
                    }
                    for msg in messages
                ]
            }
            export_data.append(session_data)
        
        # 根据格式导出
        if request.format == "json":
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            media_type = "application/json"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        elif request.format == "txt":
            lines = []
            for session_data in export_data:
                lines.append(f"========== {session_data['session']['title']} ==========")
                lines.append(f"创建时间: {session_data['session']['created_at']}")
                lines.append(f"配置: {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                lines.append("")
                for msg in session_data['messages']:
                    role_name = "用户" if msg['role'] == "user" else "助手"
                    lines.append(f"[{msg['timestamp']}] {role_name}:")
                    lines.append(msg['content'])
                    lines.append("")
                lines.append("\n")
            content = "\n".join(lines)
            media_type = "text/plain"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        elif request.format == "markdown":
            lines = []
            for session_data in export_data:
                lines.append(f"# {session_data['session']['title']}")
                lines.append(f"\n**创建时间:** {session_data['session']['created_at']}")
                lines.append(f"**配置:** {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                lines.append("\n---\n")
                for msg in session_data['messages']:
                    role_name = "🧑 用户" if msg['role'] == "user" else "🤖 助手"
                    lines.append(f"## {role_name}")
                    lines.append(f"*{msg['timestamp']}*\n")
                    lines.append(msg['content'])
                    lines.append("\n")
                lines.append("\n---\n")
            content = "\n".join(lines)
            media_type = "text/markdown"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        else:
            raise HTTPException(status_code=400, detail="不支持的导出格式")
        
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"导出错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"导出时出错: {str(e)}")

if __name__ == "__main__":
    print("🚀 启动 FastAPI 服务器...")
    print("📝 支持的 LLM: OpenAI, 智谱AI, Deepseek")
    print("💾 数据库: SQLite (chat_app.db)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
