import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from ghost_snapshot import restore_snapshot
from llm_client import create_llm_client
from message_processor import message_processor
from repositories import chat_repository, config_repository, session_repository
from models import (
    ChatMessageCreate,
    ChatRequest,
    ChatResponse,
    ChatSessionCreate,
    ChatSessionUpdate,
    ExportRequest,
    PatchRevertRequest,
)
from tools.builtin.patch_tools import ApplyPatchTool
from tools.context import reset_tool_context, set_tool_context

from ..chat_agent_runtime import run_agent_stream
from ..chat_support import (
    build_llm_user_content,
    collect_prepared_attachments,
    fallback_title,
    maybe_update_session_title,
    save_prepared_attachments,
)
from ..runtime_state import STREAM_REGISTRY
from ..session_support import schedule_ast_scan


async def chat(request: ChatRequest):
    try:
        new_session_created = False
        if request.session_id:
            session = session_repository.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = session_repository.update_session(session.id, ChatSessionUpdate(agent_profile=request.agent_profile)) or session
        else:
            config_id = request.config_id
            if not config_id:
                default_config = config_repository.get_default_config()
                if not default_config:
                    configs = config_repository.list_configs()
                    if not configs:
                        raise HTTPException(status_code=400, detail="No config available")
                    config_id = configs[0].id
                else:
                    config_id = default_config.id

            session = session_repository.create_session(
                ChatSessionCreate(
                    title="New Chat",
                    config_id=config_id,
                    work_path=request.work_path,
                    agent_type="simple",
                    agent_profile=request.agent_profile,
                    session_kind="regular",
                )
            )
            new_session_created = True
            schedule_ast_scan(session.work_path)
        is_first_turn = (session.message_count or 0) == 0

        config = config_repository.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)
        if new_session_created:
            provisional_title = fallback_title(processed_message)
            if provisional_title and provisional_title != session.title:
                session_repository.update_session(session.id, ChatSessionUpdate(title=provisional_title))

        prepared_attachments, llm_image_urls = collect_prepared_attachments(request.attachments)
        user_content = build_llm_user_content(processed_message, llm_image_urls)

        user_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="user",
                content=processed_message,
            )
        )
        save_prepared_attachments(user_msg.id, prepared_attachments)

        history = chat_repository.list_messages(session.id, limit=20)
        history_for_llm = [{"role": msg.role, "content": msg.content} for msg in history[:-1]]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role,
        )
        if llm_image_urls:
            llm_messages[-1]["content"] = user_content

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "api_format": config.api_format,
            "api_profile": config.api_profile,
        }

        llm_client = create_llm_client(config)
        llm_overrides = {
            "_debug": {
                "session_id": session.id,
                "message_id": user_msg.id,
                "agent_type": "simple",
                "iteration": 0,
            }
        }
        llm_result = await llm_client.chat(llm_messages, llm_overrides)

        llm_response = llm_result["content"]
        raw_response_data = llm_result["raw_response"]
        processed_response = message_processor.postprocess_llm_response(llm_response)

        assistant_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content=processed_response,
                raw_request=raw_request_data,
                raw_response=raw_response_data,
            )
        )
        llm_call_id = llm_result.get("llm_call_id")
        if llm_call_id:
            chat_repository.update_llm_call_processed(llm_call_id, {"content": processed_response})

        await maybe_update_session_title(
            session_id=session.id,
            config=config,
            user_message=processed_message,
            assistant_message=processed_response,
            is_first_turn=is_first_turn,
            assistant_message_id=assistant_msg.id,
        )

        return ChatResponse(reply=processed_response, session_id=session.id, message_id=assistant_msg.id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat error: {str(exc)}")


async def chat_stream(request: ChatRequest):
    try:
        new_session_created = False
        if request.session_id:
            session = session_repository.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = session_repository.update_session(session.id, ChatSessionUpdate(agent_profile=request.agent_profile)) or session
        else:
            config_id = request.config_id if request.config_id else config_repository.list_configs()[0].id
            session = session_repository.create_session(
                ChatSessionCreate(
                    title="New Chat",
                    config_id=config_id,
                    work_path=request.work_path,
                    agent_type="simple",
                    agent_profile=request.agent_profile,
                    session_kind="regular",
                )
            )
            new_session_created = True
            schedule_ast_scan(session.work_path)
        is_first_turn = (session.message_count or 0) == 0

        config = config_repository.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        processed_message = message_processor.preprocess_user_message(request.message)
        if new_session_created:
            provisional_title = fallback_title(processed_message)
            if provisional_title and provisional_title != session.title:
                session_repository.update_session(session.id, ChatSessionUpdate(title=provisional_title))

        prepared_attachments, llm_image_urls = collect_prepared_attachments(request.attachments)
        user_content = build_llm_user_content(processed_message, llm_image_urls)

        history = chat_repository.list_messages(session.id, limit=20)
        history_for_llm = [{"role": msg.role, "content": msg.content} for msg in history]

        system_role = "developer" if config.api_profile == "openai" else "system"
        llm_messages = message_processor.build_messages_for_llm(
            user_message=processed_message,
            history=history_for_llm,
            system_prompt="You are a helpful AI assistant.",
            system_role=system_role,
        )
        if llm_image_urls:
            llm_messages[-1]["content"] = user_content

        raw_request_data = {
            "model": config.model,
            "messages": llm_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "api_format": config.api_format,
            "api_profile": config.api_profile,
        }

        user_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="user",
                content=processed_message,
                raw_request=raw_request_data,
            )
        )
        saved_attachments = save_prepared_attachments(user_msg.id, prepared_attachments)

        async def generate():
            yield f"data: {json.dumps({'session_id': session.id, 'user_message_id': user_msg.id, 'user_attachments': saved_attachments})}\n\n"
            full_response = ""
            try:
                llm_client = create_llm_client(config)
                llm_overrides = {
                    "_debug": {
                        "session_id": session.id,
                        "message_id": user_msg.id,
                        "agent_type": "simple",
                        "iteration": 0,
                    }
                }

                async for chunk in llm_client.chat_stream(llm_messages, llm_overrides):
                    full_response += chunk
                    yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

                processed_response = message_processor.postprocess_llm_response(full_response)

                assistant_msg = chat_repository.create_message(
                    ChatMessageCreate(
                        session_id=session.id,
                        role="assistant",
                        content=processed_response,
                        raw_response={
                            "content": processed_response,
                            "model": config.model,
                            "finish_reason": "stop",
                        },
                    )
                )
                llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")
                if llm_call_id:
                    chat_repository.update_llm_call_processed(llm_call_id, {"content": processed_response})

                await maybe_update_session_title(
                    session_id=session.id,
                    config=config,
                    user_message=processed_message,
                    assistant_message=processed_response,
                    is_first_turn=is_first_turn,
                    assistant_message_id=assistant_msg.id,
                )

                yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg.id})}\n\n"
            except Exception as exc:
                if full_response:
                    chat_repository.create_message(
                        ChatMessageCreate(
                            session_id=session.id,
                            role="assistant",
                            content=full_response + "\n\n[stream interrupted]",
                            metadata={"error": str(exc), "partial": True},
                        )
                    )
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def export_chat_history(request: ExportRequest):
    try:
        if request.session_id:
            session = session_repository.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            sessions = [session]
        else:
            sessions = session_repository.list_sessions()

        export_data = []
        for session in sessions:
            messages = chat_repository.list_messages(session.id)
            config = config_repository.get_config(session.config_id)

            session_data = {
                "session": {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at,
                    "context_summary": getattr(session, "context_summary", None),
                    "last_compressed_llm_call_id": getattr(session, "last_compressed_llm_call_id", None),
                    "config": {
                        "name": config.name if config else "unknown",
                        "model": config.model if config else "unknown",
                    },
                },
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                    }
                    for msg in messages
                ],
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
                if session_data["session"].get("context_summary"):
                    lines.append("Context Summary:")
                    lines.append(session_data["session"]["context_summary"])
                    lines.append("")
                lines.append("")
                for msg in session_data["messages"]:
                    role_name = "User" if msg["role"] == "user" else "Assistant"
                    lines.append(f"[{msg['timestamp']}] {role_name}:")
                    lines.append(msg["content"])
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
                if session_data["session"].get("context_summary"):
                    lines.append("\n**Context Summary:**")
                    lines.append(session_data["session"]["context_summary"])
                lines.append("\n---\n")
                for msg in session_data["messages"]:
                    role_name = "User" if msg["role"] == "user" else "Assistant"
                    lines.append(f"## {role_name}")
                    lines.append(f"*{msg['timestamp']}*\n")
                    lines.append(msg["content"])
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
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export error: {str(exc)}")


def _resolve_stream_keepalive_sec() -> int:
    keepalive_sec = 15
    try:
        keepalive_sec = int(os.getenv("AGENT_STREAM_KEEPALIVE_SEC", "15") or "15")
    except (TypeError, ValueError):
        keepalive_sec = 15
    if keepalive_sec < 5:
        keepalive_sec = 5
    return keepalive_sec


async def chat_agent_stream(request: ChatRequest):
    try:
        if request.resume and request.stream_id:
            state = await STREAM_REGISTRY.get(request.stream_id)
            if not state:
                raise HTTPException(status_code=404, detail="Stream not found")
            last_seq = request.last_seq or 0
            return StreamingResponse(
                state.stream(last_seq),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        keepalive_sec = _resolve_stream_keepalive_sec()
        state = await STREAM_REGISTRY.create(keepalive_sec)
        asyncio.create_task(run_agent_stream(request, state))
        last_seq = request.last_seq or 0
        return StreamingResponse(
            state.stream(last_seq),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(exc)}")


async def revert_patch(request: PatchRevertRequest):
    session = session_repository.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot_restored = False
    snapshot_error = None
    if request.message_id:
        snapshot = chat_repository.get_file_snapshot(session.id, request.message_id)
        if snapshot:
            try:
                restore_snapshot(snapshot.get("tree_hash"), snapshot.get("work_path"))
                snapshot_restored = True
            except Exception as exc:
                snapshot_error = str(exc)

    result_text = ""
    result: Dict[str, Any] = {"ok": False}

    if not snapshot_restored:
        token = set_tool_context(
            {
                "shell_unrestricted": False,
                "agent_mode": "default",
                "session_id": session.id,
                "work_path": session.work_path,
            }
        )
        try:
            tool = ApplyPatchTool()
            result_text = await tool.execute(json.dumps({"patch": request.revert_patch}))
        finally:
            reset_tool_context(token)

        try:
            result = json.loads(result_text)
        except Exception:
            result = {"ok": False, "error": result_text}

        if not result.get("ok"):
            detail = result.get("error", "Patch revert failed")
            if snapshot_error:
                detail = f"{detail} (snapshot restore failed: {snapshot_error})"
            raise HTTPException(status_code=400, detail=detail)
    else:
        result = {"ok": True, "snapshot_restored": True}
        result_text = json.dumps(result, ensure_ascii=False)

    user_msg = chat_repository.create_message(
        ChatMessageCreate(
            session_id=session.id,
            role="user",
            content="Revert the latest apply_patch change",
            metadata={"action": "revert_patch"},
        )
    )

    assistant_msg = chat_repository.create_message(
        ChatMessageCreate(
            session_id=session.id,
            role="assistant",
            content="Reverted the latest patch change.",
        )
    )

    chat_repository.save_agent_step(
        message_id=assistant_msg.id,
        step_type="observation",
        content=result_text,
        sequence=0,
        metadata={
            "tool": "apply_patch" if not snapshot_restored else "snapshot_restore",
            "patch_event": "revert",
        },
    )
    chat_repository.save_agent_step(
        message_id=assistant_msg.id,
        step_type="answer",
        content="Reverted the latest patch change.",
        sequence=1,
        metadata={"patch_event": "revert"},
    )

    return {
        "ok": True,
        "result": result,
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id,
    }




