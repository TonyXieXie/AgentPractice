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

from agents.executor import create_agent_executor
from agents.base import AgentStep
from tools.builtin import register_builtin_tools
from tools.base import ToolRegistry

app = FastAPI(title="Tauri Agent Chat Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_builtin_tools()

# ==================== Base routes ====================

@app.get("/")
def read_root():
    return {"status": "FastAPI is running!", "version": "2.0"}

# ==================== LLM Configs ====================

@app.get("/configs", response_model=List[LLMConfig])
def get_configs():
    return db.get_all_configs()

@app.get("/configs/default", response_model=LLMConfig)
def get_default_config():
    config = db.get_default_config()
    if not config:
        configs = db.get_all_configs()
        if configs:
            return configs[0]
        raise HTTPException(status_code=404, detail="No config available")
    return config

@app.get("/configs/{config_id}", response_model=LLMConfig)
def get_config(config_id: str):
    config = db.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config

@app.post("/configs", response_model=LLMConfig)
def create_config(config: LLMConfigCreate):
    return db.create_config(config)

@app.put("/configs/{config_id}", response_model=LLMConfig)
def update_config(config_id: str, update: LLMConfigUpdate):
    config = db.update_config(config_id, update)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config

@app.delete("/configs/{config_id}")
def delete_config(config_id: str):
    sessions = db.get_all_sessions()
    if any(s.config_id == config_id for s in sessions):
        raise HTTPException(status_code=400, detail="Config is in use by sessions")

    if db.delete_config(config_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Config not found")

# ==================== Sessions ====================

@app.get("/sessions", response_model=List[ChatSession])
def get_sessions():
    return db.get_all_sessions()

@app.get("/sessions/{session_id}", response_model=ChatSession)
def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.post("/sessions", response_model=ChatSession)
def create_session(session: ChatSessionCreate):
    config = db.get_config(session.config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return db.create_session(session)

@app.put("/sessions/{session_id}", response_model=ChatSession)
def update_session(session_id: str, update: ChatSessionUpdate):
    session = db.update_session(session_id, update)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if db.delete_session(session_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Session not found")

@app.get("/sessions/{session_id}/messages", response_model=List[ChatMessage])
def get_session_messages(session_id: str, limit: Optional[int] = None):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_messages(session_id, limit)

@app.get("/sessions/{session_id}/llm_calls")
def get_session_llm_calls(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_llm_calls(session_id)

@app.get("/sessions/{session_id}/agent_steps")
def get_session_agent_steps(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_session_agent_steps(session_id)

# ==================== Chat ====================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
        else:
            config_id = request.config_id
            if not config_id:
                default_config = db.get_default_config()
                if not default_config:
                    configs = db.get_all_configs()
                    if not configs:
                        raise HTTPException(status_code=400, detail="No config available")
                    config_id = configs[0].id
                else:
                    config_id = default_config.id

            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id
            ))

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))

        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history[:-1]
        ]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role
        )

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "api_format": config.api_format,
            "api_profile": config.api_profile
        }
        if request.response_format is not None:
            raw_request_data["response_format"] = request.response_format

        llm_client = create_llm_client(config)
        llm_overrides = {}
        if request.response_format is not None:
            llm_overrides["response_format"] = request.response_format
        llm_overrides["_debug"] = {
            "session_id": session.id,
            "message_id": user_msg.id,
            "agent_type": "simple",
            "iteration": 0
        }
        llm_result = await llm_client.chat(llm_messages, llm_overrides)

        llm_response = llm_result["content"]
        raw_response_data = llm_result["raw_response"]

        processed_response = message_processor.postprocess_llm_response(llm_response)

        assistant_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="assistant",
            content=processed_response,
            raw_request=raw_request_data,
            raw_response=raw_response_data
        ))
        llm_call_id = llm_result.get("llm_call_id")
        if llm_call_id:
            db.update_llm_call_processed(llm_call_id, {"content": processed_response})

        if session.message_count == 0:
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
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    try:
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
        else:
            config_id = request.config_id if request.config_id else db.get_all_configs()[0].id
            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id
            ))

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)

        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history
        ]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role
        )

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "api_format": config.api_format,
            "api_profile": config.api_profile
        }
        if request.response_format is not None:
            raw_request_data["response_format"] = request.response_format

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message,
            raw_request=raw_request_data
        ))

        async def generate():
            yield f"data: {json.dumps({'session_id': session.id, 'user_message_id': user_msg.id})}\n\n"
            full_response = ""
            try:
                llm_client = create_llm_client(config)
                llm_overrides = {}
                if request.response_format is not None:
                    llm_overrides["response_format"] = request.response_format
                llm_overrides["_debug"] = {
                    "session_id": session.id,
                    "message_id": user_msg.id,
                    "agent_type": "simple",
                    "iteration": 0
                }

                async for chunk in llm_client.chat_stream(llm_messages, llm_overrides):
                    full_response += chunk
                    yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

                processed_response = message_processor.postprocess_llm_response(full_response)

                assistant_msg = db.create_message(ChatMessageCreate(
                    session_id=session.id,
                    role="assistant",
                    content=processed_response,
                    raw_response={
                        "content": processed_response,
                        "model": config.model,
                        "finish_reason": "stop"
                    }
                ))
                llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")
                if llm_call_id:
                    db.update_llm_call_processed(llm_call_id, {"content": processed_response})

                yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg.id})}\n\n"
            except Exception as e:
                if full_response:
                    db.create_message(ChatMessageCreate(
                        session_id=session.id,
                        role="assistant",
                        content=full_response + "\n\n[stream interrupted]",
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

# ==================== Export ====================

@app.post("/export")
def export_chat_history(request: ExportRequest):
    try:
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            sessions = [session]
        else:
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
                        "name": config.name if config else "unknown",
                        "model": config.model if config else "unknown"
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

        if request.format == "json":
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            media_type = "application/json"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        elif request.format == "txt":
            lines = []
            for session_data in export_data:
                lines.append(f"========== {session_data['session']['title']} ==========")
                lines.append(f"Created: {session_data['session']['created_at']}")
                lines.append(f"Config: {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                lines.append("")
                for msg in session_data['messages']:
                    role_name = "User" if msg['role'] == "user" else "Assistant"
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
                lines.append(f"\n**Created:** {session_data['session']['created_at']}")
                lines.append(f"**Config:** {session_data['session']['config']['name']} ({session_data['session']['config']['model']})")
                lines.append("\n---\n")
                for msg in session_data['messages']:
                    role_name = "User" if msg['role'] == "user" else "Assistant"
                    lines.append(f"## {role_name}")
                    lines.append(f"*{msg['timestamp']}*\n")
                    lines.append(msg['content'])
                    lines.append("\n")
                lines.append("\n---\n")
            content = "\n".join(lines)
            media_type = "text/markdown"
            filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        else:
            raise HTTPException(status_code=400, detail="Unsupported export format")

        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")

# ==================== Agent Chat (Streaming) ====================

@app.post("/chat/agent/stream")
async def chat_agent_stream(request: ChatRequest):
    try:
        if request.session_id:
            session = db.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
        else:
            config_id = request.config_id or db.get_default_config().id
            session = db.create_session(ChatSessionCreate(
                title="New Chat",
                config_id=config_id
            ))

        config = db.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)

        user_msg = db.create_message(ChatMessageCreate(
            session_id=session.id,
            role="user",
            content=processed_message
        ))

        history = db.get_session_messages(session.id, limit=20)
        history_for_llm = [
            {"role": msg.role, "content": msg.content}
            for msg in history[:-1]
        ]

        agent_type = request.agent_type_override if hasattr(request, 'agent_type_override') else getattr(session, 'agent_type', 'react')
        tools = ToolRegistry.get_all()

        llm_client = create_llm_client(config)

        try:
            executor = create_agent_executor(
                agent_type=agent_type,
                llm_client=llm_client,
                tools=tools,
                max_iterations=5
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        def stream_text_chunks(text: str, chunk_size: int = 1):
            if not text:
                return
            for i in range(0, len(text), chunk_size):
                yield text[i:i + chunk_size]

        async def event_generator():
            sequence = 0
            final_answer = None
            assistant_msg_id = None
            saw_delta = False

            try:
                temp_assistant_msg = db.create_message(ChatMessageCreate(
                    session_id=session.id,
                    role="assistant",
                    content=""
                ))
                assistant_msg_id = temp_assistant_msg.id
                yield f"data: {json.dumps({'session_id': session.id, 'user_message_id': user_msg.id, 'assistant_message_id': assistant_msg_id})}\n\n"
                request_overrides = {
                    "_debug": {
                        "session_id": session.id,
                        "message_id": assistant_msg_id
                    }
                }
                if request.response_format is not None:
                    request_overrides["response_format"] = request.response_format

                async for step in executor.run(
                    user_input=processed_message,
                    history=history_for_llm,
                    session_id=session.id,
                    request_overrides=request_overrides if request_overrides else None
                ):
                    if step.step_type.endswith("_delta"):
                        saw_delta = True
                        yield f"data: {json.dumps(step.to_dict())}\n\n"
                        continue

                    db.save_agent_step(
                        message_id=assistant_msg_id,
                        step_type=step.step_type,
                        content=step.content,
                        sequence=sequence,
                        metadata=step.metadata
                    )

                    if step.step_type == "action" and "tool" in step.metadata:
                        db.save_tool_call(
                            message_id=assistant_msg_id,
                            tool_name=step.metadata["tool"],
                            tool_input=step.metadata.get("input", ""),
                            tool_output=""
                        )

                    if step.step_type == "answer":
                        final_answer = step.content
                        if not saw_delta:
                            for chunk in stream_text_chunks(step.content, chunk_size=1):
                                yield f"data: {json.dumps({'step_type': 'answer_delta', 'content': chunk, 'metadata': step.metadata})}\n\n"
                        yield f"data: {json.dumps(step.to_dict())}\n\n"
                        sequence += 1
                        continue

                    if step.step_type == "error":
                        final_answer = step.content

                    yield f"data: {json.dumps(step.to_dict())}\n\n"
                    sequence += 1

                if final_answer and assistant_msg_id:
                    conn = db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE chat_messages
                        SET content = ?
                        WHERE id = ?
                    ''', (final_answer, assistant_msg_id))
                    conn.commit()
                    conn.close()

                yield f"data: {json.dumps({'done': True, 'session_id': session.id})}\n\n"

            except Exception as e:
                error_step = AgentStep(
                    step_type="error",
                    content=f"Agent failed: {str(e)}",
                    metadata={"error": str(e)}
                )
                yield f"data: {json.dumps(error_step.to_dict())}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

# ==================== Tools ====================

@app.get("/tools")
def get_tools():
    tools = ToolRegistry.get_all()
    return [tool.to_dict() for tool in tools]

if __name__ == "__main__":
    print("Starting FastAPI server...")
    print("Supported LLMs: OpenAI, ZhipuAI, Deepseek")
    print("Database: SQLite (chat_app.db)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
