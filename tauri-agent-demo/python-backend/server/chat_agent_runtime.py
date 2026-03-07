import asyncio
import traceback
from typing import Any, Dict, Optional

from fastapi import HTTPException

from agents.base import AgentStep
from agents.executor import create_agent_executor
from agents.prompt_builder import build_agent_prompt_and_tools
from app_config import get_app_config
from code_map import build_code_map_prompt
from context_compress import build_history_for_llm, maybe_compress_context
from llm_client import create_llm_client
from message_processor import message_processor
from models import ChatMessageCreate, ChatRequest, ChatSessionCreate, ChatSessionUpdate
from repositories import chat_repository, config_repository, session_repository
from skills import build_skill_prompt_sections, extract_skill_invocations, get_enabled_skills
from stream_control import stream_stop_registry
from tools.base import ToolRegistry

from .agent_prompt_support import append_reasoning_summary_prompt, build_live_pty_prompt
from .chat_support import (
    build_llm_user_content,
    collect_prepared_attachments,
    fallback_title,
    maybe_update_session_title,
    save_prepared_attachments,
)
from .session_support import schedule_ast_scan


def _stream_text_chunks(text: str, chunk_size: int = 1):
    if not text:
        return
    for index in range(0, len(text), chunk_size):
        yield text[index:index + chunk_size]


async def run_agent_stream(request: ChatRequest, state: Any) -> None:
    new_session_created = False
    assistant_msg_id: Optional[int] = None
    try:
        await state.emit({"stream_id": state.stream_id})
        if request.session_id:
            session = session_repository.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if request.agent_profile is not None and request.agent_profile != getattr(session, "agent_profile", None):
                session = session_repository.update_session(
                    session.id,
                    ChatSessionUpdate(agent_profile=request.agent_profile),
                ) or session
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
                    agent_profile=request.agent_profile,
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
        saved_attachments = save_prepared_attachments(user_msg.id, prepared_attachments)

        context_summary = getattr(session, "context_summary", None) or ""
        last_compressed_call_id = getattr(session, "last_compressed_llm_call_id", None)
        last_compressed_message_id = (
            chat_repository.get_max_message_id_for_llm_call(session.id, int(last_compressed_call_id or 0))
            if last_compressed_call_id
            else None
        )

        app_config = get_app_config()
        llm_app_config = app_config.get("llm", {}) if isinstance(app_config, dict) else {}
        global_reasoning_summary = llm_app_config.get("reasoning_summary")
        if global_reasoning_summary:
            try:
                config.reasoning_summary = str(global_reasoning_summary)
            except Exception:
                pass

        agent_type = request.agent_type_override if hasattr(request, "agent_type_override") else getattr(session, "agent_type", "react")
        profile_id = request.agent_profile or getattr(session, "agent_profile", None)
        include_tools = agent_type != "simple"
        pty_prompt = build_live_pty_prompt(session.id)
        system_prompt, tools, resolved_profile_id, ability_ids = build_agent_prompt_and_tools(
            profile_id,
            ToolRegistry.get_all(),
            include_tools=include_tools,
            extra_context={"pty_sessions": pty_prompt},
            exclude_ability_ids=["code_map"],
        )
        enabled_skills = get_enabled_skills()
        invoked_skill_names = extract_skill_invocations(processed_message, max_count=1)
        invoked_skills = []
        if enabled_skills and invoked_skill_names:
            skill_map = {skill.name.lower(): skill for skill in enabled_skills}
            for name in invoked_skill_names:
                skill = skill_map.get(name.lower())
                if skill:
                    invoked_skills.append(skill)
        skills_prompt = build_skill_prompt_sections([], invoked_skills)
        system_prompt = append_reasoning_summary_prompt(system_prompt, global_reasoning_summary)
        if resolved_profile_id and resolved_profile_id != getattr(session, "agent_profile", None):
            session_repository.update_session(session.id, ChatSessionUpdate(agent_profile=resolved_profile_id))

        llm_client = create_llm_client(config)
        agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        context_config = app_config.get("context", {}) if isinstance(app_config, dict) else {}
        ast_enabled = bool(agent_config.get("ast_enabled", True))
        code_map_cfg = agent_config.get("code_map", {}) if isinstance(agent_config, dict) else {}
        code_map_enabled = bool(code_map_cfg.get("enabled", True))
        code_map_prompt = None
        if "code_map" in ability_ids and ast_enabled and code_map_enabled:
            code_map_prompt = build_code_map_prompt(
                session.id,
                request.work_path or getattr(session, "work_path", None),
            )
        react_max_iterations = agent_config.get("react_max_iterations", 50)
        try:
            react_max_iterations = int(react_max_iterations)
        except (TypeError, ValueError):
            react_max_iterations = 50

        try:
            executor = create_agent_executor(
                agent_type=agent_type,
                llm_client=llm_client,
                tools=tools,
                max_iterations=react_max_iterations,
                system_prompt=system_prompt,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        sequence = 0
        final_answer = None
        saw_delta = False
        prompt_truncation_cfg = {
            "enabled": bool(context_config.get("truncate_long_data", True)),
            "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
            "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
            "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800),
        }

        pending_compress_step = None
        if agent_type != "react":
            updated_summary, updated_call_id, updated_message_id, did_compress = await maybe_compress_context(
                session_id=session.id,
                config=config,
                app_config=app_config,
                llm_client=llm_client,
                current_summary=context_summary,
                last_compressed_call_id=last_compressed_call_id,
                current_user_message_id=user_msg.id,
                current_user_text=processed_message,
            )
            if did_compress:
                context_summary = updated_summary
                last_compressed_call_id = updated_call_id
                last_compressed_message_id = updated_message_id
                try:
                    chat_repository.update_session_context(session.id, context_summary, last_compressed_call_id)
                except Exception as exc:
                    print(f"[Context Compress] Failed to update session context: {exc}")
                pending_compress_step = AgentStep(
                    step_type="observation",
                    content="Compressing previous context...",
                    metadata={"context_compress": True},
                )

        history_for_llm = build_history_for_llm(
            session.id,
            last_compressed_message_id,
            user_msg.id,
            context_summary,
            code_map_prompt,
            prompt_truncation_cfg,
        )
        if skills_prompt:
            skill_meta = {"skill_prompt": True}
            if invoked_skills:
                skill_meta["skill_name"] = invoked_skills[0].name
                skill_meta["skill_path"] = invoked_skills[0].path
            chat_repository.create_message(
                ChatMessageCreate(
                    session_id=session.id,
                    role="user",
                    content=skills_prompt,
                    metadata=skill_meta,
                )
            )

        temp_assistant_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="",
            )
        )
        assistant_msg_id = temp_assistant_msg.id
        stop_event = stream_stop_registry.create(assistant_msg_id)
        init_payload = {
            "session_id": session.id,
            "user_message_id": user_msg.id,
            "assistant_message_id": assistant_msg_id,
            "user_attachments": saved_attachments,
        }
        await state.set_init_payload(init_payload)
        await state.emit(init_payload)
        request_overrides: Dict[str, Any] = {
            "_debug": {"session_id": session.id, "message_id": assistant_msg_id},
            "work_path": request.work_path or getattr(session, "work_path", None),
            "prompt_truncation": prompt_truncation_cfg,
            "_stop_event": stop_event,
            "_context_state": {
                "summary": context_summary,
                "last_call_id": last_compressed_call_id,
                "last_message_id": last_compressed_message_id,
                "current_user_message_id": user_msg.id,
            },
        }
        if request.extra_work_paths:
            request_overrides["extra_work_paths"] = [str(path) for path in request.extra_work_paths if path]
        if llm_image_urls:
            request_overrides["user_content"] = user_content
        if request.agent_mode is not None:
            request_overrides["agent_mode"] = request.agent_mode
        if request.shell_unrestricted is not None:
            request_overrides["shell_unrestricted"] = request.shell_unrestricted
        if code_map_prompt:
            request_overrides["_code_map_prompt"] = code_map_prompt
        if skills_prompt:
            request_overrides["_post_user_messages"] = [{"role": "user", "content": skills_prompt}]

        if pending_compress_step:
            chat_repository.save_agent_step(
                message_id=assistant_msg_id,
                step_type=pending_compress_step.step_type,
                content=pending_compress_step.content,
                sequence=sequence,
                metadata=pending_compress_step.metadata,
            )
            await state.emit(pending_compress_step.to_dict())
            sequence += 1

        step_iter = executor.run(
            user_input=processed_message,
            history=history_for_llm,
            session_id=session.id,
            request_overrides=request_overrides,
        )
        step_queue: asyncio.Queue = asyncio.Queue()

        async def _produce_steps() -> None:
            try:
                async for step in step_iter:
                    await step_queue.put(step)
            finally:
                await step_queue.put(None)

        producer_task = asyncio.create_task(_produce_steps())
        try:
            while True:
                step = await step_queue.get()
                if step is None:
                    break

                if step.step_type == "context_estimate":
                    try:
                        session_repository.update_context_estimate(session.id, step.metadata)
                    except Exception as exc:
                        print(f"[Context Estimate] Failed to update session: {exc}")
                    await state.emit(step.to_dict())
                    continue

                if step.step_type.endswith("_delta"):
                    saw_delta = True
                    await state.emit(step.to_dict())
                    continue

                suppress_prompt = False
                if step.step_type == "error":
                    suppress_prompt = bool(step.metadata.get("suppress_prompt")) if isinstance(step.metadata, dict) else False

                if suppress_prompt:
                    await state.emit(step.to_dict())
                    continue

                chat_repository.save_agent_step(
                    message_id=assistant_msg_id,
                    step_type=step.step_type,
                    content=step.content,
                    sequence=sequence,
                    metadata=step.metadata,
                )

                if step.step_type == "action" and isinstance(step.metadata, dict) and "tool" in step.metadata:
                    chat_repository.save_tool_call(
                        message_id=assistant_msg_id,
                        tool_name=step.metadata["tool"],
                        tool_input=step.metadata.get("input", ""),
                        tool_output="",
                    )

                if step.step_type == "answer":
                    final_answer = step.content
                    if not saw_delta:
                        for chunk in _stream_text_chunks(step.content, chunk_size=1):
                            await state.emit({"step_type": "answer_delta", "content": chunk, "metadata": step.metadata})
                    await state.emit(step.to_dict())
                    sequence += 1
                    continue

                if step.step_type == "error":
                    final_answer = step.content

                await state.emit(step.to_dict())
                sequence += 1
        finally:
            if producer_task and not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except Exception:
                    pass

        if final_answer and assistant_msg_id:
            chat_repository.update_message_content(session.id, int(assistant_msg_id), final_answer)
            await maybe_update_session_title(
                session_id=session.id,
                config=config,
                user_message=processed_message,
                assistant_message=final_answer,
                is_first_turn=is_first_turn,
                assistant_message_id=assistant_msg_id,
            )

        await state.emit({"done": True, "session_id": session.id})
    except Exception as exc:
        error_step = AgentStep(
            step_type="error",
            content=f"Agent failed: {str(exc)}",
            metadata={"error": str(exc), "traceback": traceback.format_exc()},
        )
        try:
            await state.emit(error_step.to_dict())
        except Exception:
            pass
    finally:
        if assistant_msg_id:
            stream_stop_registry.clear(assistant_msg_id)
        try:
            await state.mark_done()
        except Exception:
            pass


__all__ = ["run_agent_stream"]
