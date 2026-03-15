import asyncio
import json
import traceback
from typing import Any, Dict, Optional

from fastapi import HTTPException

from agent_team import AgentTeam
from agents.base import AgentStep
from agents.executor import create_agent_executor
from agents.prompt_builder import build_agent_prompt_and_tools
from app_config import get_app_config
from code_map import build_code_map_prompt
from context_compress import (
    build_history_for_llm,
    maybe_compress_context,
    maybe_compress_private_context,
)
from llm_client import create_llm_client
from message_processor import message_processor
from models import ChatMessageCreate, ChatRequest, ChatSessionCreate, ChatSessionUpdate
from repositories import chat_repository, config_repository, session_repository
from skills import build_skill_prompt_sections, extract_skill_invocations, get_enabled_skills
from stream_control import stream_stop_registry
from team_coordinator import TeamCoordinator
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


def _build_prompt_truncation_config(context_config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "enabled": bool(context_config.get("truncate_long_data", True)),
        "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
        "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
        "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800),
    }


def _build_context_step(global_changed: bool, private_changed: bool) -> Optional[AgentStep]:
    if not global_changed and not private_changed:
        return None
    if global_changed and private_changed:
        scope = "global_private"
    elif global_changed:
        scope = "global"
    else:
        scope = "private"
    return AgentStep(
        step_type="observation",
        content="Compressing previous context...",
        metadata={"context_compress": True, "scope": scope},
    )


def _extract_handoff_request(step: AgentStep) -> Optional[Dict[str, str]]:
    metadata = step.metadata if isinstance(step.metadata, dict) else {}
    if metadata.get("handoff_requested"):
        target_agent = str(metadata.get("target_agent") or metadata.get("to_agent") or "").strip()
        if not target_agent:
            return None
        return {
            "from_agent": str(metadata.get("from_agent") or "").strip(),
            "target_agent": target_agent,
            "reason": str(metadata.get("reason") or "").strip(),
            "work_summary": str(metadata.get("work_summary") or "").strip(),
        }

    tool_name = str(metadata.get("tool") or "").strip().lower()
    if tool_name != "handoff":
        return None

    try:
        payload = json.loads(str(step.content or ""))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "").strip().lower() != "handoff":
        return None

    target_agent = str(payload.get("target_agent") or "").strip()
    if not target_agent:
        return None
    return {
        "from_agent": str(payload.get("from_agent") or "").strip(),
        "target_agent": target_agent,
        "reason": str(payload.get("reason") or "").strip(),
        "work_summary": str(payload.get("work_summary") or "").strip(),
    }


def _build_invalid_leader_answer_guidance(current_profile: str) -> str:
    return (
        f"Runtime instruction for {current_profile}: your final answer must be user-facing. "
        "Do not return raw handoff logs, event markers, or '[Role] Handoff to [...]' transcript text. "
        "Summarize the decision in normal dialogue or ask the user for the missing information."
    )


def _looks_like_handoff_transcript(text: Optional[str]) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    first_line = normalized.splitlines()[0].strip()
    return "Handoff to [" in first_line and "Status:" in normalized


def _build_streaming_assistant_metadata(active_agent_profile: Optional[str] = None) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "agent_steps": [],
        "agent_streaming": True,
        "agent_answer_buffers": {},
        "agent_thought_buffers": {},
        "agent_action_buffers": {},
        "agent_observation_buffers": {},
    }
    if active_agent_profile:
        metadata["agent_profile"] = active_agent_profile
    return metadata


async def _switch_streaming_assistant_message(
    state: Any,
    session_id: str,
    previous_message_id: int,
    active_agent_profile: Optional[str] = None,
) -> int:
    previous_payload = chat_repository.get_message_details(session_id, previous_message_id) or {}
    previous_content = str(previous_payload.get("content") or "")
    previous_metadata = previous_payload.get("metadata") if isinstance(previous_payload.get("metadata"), dict) else {}
    finalized_metadata: Dict[str, Any] = {
        **previous_metadata,
        "agent_streaming": False,
        "agent_answer_buffers": {},
        "agent_thought_buffers": {},
        "agent_action_buffers": {},
        "agent_observation_buffers": {},
    }
    if active_agent_profile:
        finalized_metadata["agent_profile"] = active_agent_profile
    chat_repository.update_message_content_and_metadata(
        session_id,
        previous_message_id,
        previous_content,
        finalized_metadata,
    )
    updated_previous = chat_repository.get_message_details(session_id, previous_message_id)
    if updated_previous:
        payload: Dict[str, Any] = {"session_id": session_id, "message": updated_previous}
        if active_agent_profile:
            payload["active_agent_profile"] = active_agent_profile
        await state.emit(payload)

    new_message = chat_repository.create_message(
        ChatMessageCreate(
            session_id=session_id,
            role="assistant",
            content="",
            metadata=_build_streaming_assistant_metadata(active_agent_profile),
        )
    )
    new_payload = chat_repository.get_message_details(session_id, int(new_message.id)) or new_message.model_dump()
    message_payload: Dict[str, Any] = {"session_id": session_id, "message": new_payload}
    if active_agent_profile:
        message_payload["active_agent_profile"] = active_agent_profile
    await state.emit(message_payload)

    control_payload: Dict[str, Any] = {"session_id": session_id, "assistant_message_id": int(new_message.id)}
    if active_agent_profile:
        control_payload["active_agent_profile"] = active_agent_profile
    await state.emit(control_payload)
    return int(new_message.id)


async def run_agent_stream(request: ChatRequest, state: Any) -> None:
    new_session_created = False
    assistant_msg_id: Optional[int] = None
    try:
        await state.emit({"stream_id": state.stream_id})

        if request.agent_profile and request.agent_team_id:
            raise HTTPException(status_code=400, detail="agent_profile and agent_team_id cannot both be set")

        app_config = get_app_config()
        team = AgentTeam(app_config)
        coordinator = TeamCoordinator(app_config)

        if request.session_id:
            session = session_repository.get_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            selection = team.resolve_session_selection(
                session_profile=getattr(session, "agent_profile", None),
                session_team_id=getattr(session, "agent_team_id", None),
                requested_profile=request.agent_profile,
                requested_team_id=request.agent_team_id,
            )
            if (
                selection.get("agent_profile") != getattr(session, "agent_profile", None)
                or selection.get("agent_team_id") != getattr(session, "agent_team_id", None)
            ):
                session = session_repository.update_session(
                    session.id,
                    ChatSessionUpdate(
                        agent_profile=selection.get("agent_profile"),
                        agent_team_id=selection.get("agent_team_id"),
                    ),
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
            selection = team.resolve_session_selection(
                requested_profile=request.agent_profile,
                requested_team_id=request.agent_team_id,
            )
            session = session_repository.create_session(
                ChatSessionCreate(
                    title="New Chat",
                    config_id=config_id,
                    work_path=request.work_path,
                    agent_profile=selection.get("agent_profile"),
                    agent_team_id=selection.get("agent_team_id"),
                )
            )
            new_session_created = True
            schedule_ast_scan(session.work_path)

        is_first_turn = (session.message_count or 0) == 0
        config = config_repository.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        active_selection = team.resolve_session_selection(
            session_profile=getattr(session, "agent_profile", None),
            session_team_id=getattr(session, "agent_team_id", None),
            requested_profile=request.agent_profile,
            requested_team_id=request.agent_team_id,
        )
        active_profile = active_selection.get("agent_profile")
        active_team_id = active_selection.get("agent_team_id")
        if (
            active_profile != getattr(session, "agent_profile", None)
            or active_team_id != getattr(session, "agent_team_id", None)
        ):
            session = session_repository.update_session(
                session.id,
                ChatSessionUpdate(agent_profile=active_profile, agent_team_id=active_team_id),
            ) or session

        if coordinator.is_multi_session_enabled() and getattr(session, "team_id", None):
            current_role = str(getattr(session, "role_key", None) or getattr(session, "agent_profile", None) or "").strip()
            leader_role = coordinator.resolve_leader_role(getattr(session, "agent_team_id", None), fallback_role=None)
            if leader_role and current_role and current_role != leader_role:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This role session is reserved for team collaboration history. "
                        f"Start new user tasks from the leader session ({leader_role})."
                    ),
                )

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

        llm_app_config = app_config.get("llm", {}) if isinstance(app_config, dict) else {}
        global_reasoning_summary = llm_app_config.get("reasoning_summary")
        if global_reasoning_summary:
            try:
                config.reasoning_summary = str(global_reasoning_summary)
            except Exception:
                pass

        agent_type = (
            request.agent_type_override
            if hasattr(request, "agent_type_override")
            else getattr(session, "agent_type", "react")
        )
        include_tools = agent_type != "simple"

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
        post_user_messages = None
        if skills_prompt:
            post_user_messages = [{"role": "user", "content": skills_prompt}]

        llm_client = create_llm_client(config)
        agent_config = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
        context_config = app_config.get("context", {}) if isinstance(app_config, dict) else {}
        ast_enabled = bool(agent_config.get("ast_enabled", True))
        code_map_cfg = agent_config.get("code_map", {}) if isinstance(agent_config, dict) else {}
        code_map_enabled = bool(code_map_cfg.get("enabled", True))
        react_max_iterations = agent_config.get("react_max_iterations", 50)
        try:
            react_max_iterations = int(react_max_iterations)
        except (TypeError, ValueError):
            react_max_iterations = 50

        temp_assistant_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session.id,
                role="assistant",
                content="",
                metadata=_build_streaming_assistant_metadata(active_profile),
            )
        )
        assistant_msg_id = temp_assistant_msg.id
        stop_event = stream_stop_registry.create(assistant_msg_id)

        init_payload = {
            "session_id": session.id,
            "user_message_id": user_msg.id,
            "assistant_message_id": assistant_msg_id,
            "user_attachments": saved_attachments,
            "active_agent_profile": active_profile,
            "agent_team_id": getattr(session, "agent_team_id", None),
        }
        await state.set_init_payload(init_payload)
        await state.emit(init_payload)

        final_answer: Optional[str] = None
        final_agent_profile: Optional[str] = None
        sequence = 0
        handoff_count = 0
        max_handoffs = 16

        while True:
            current_selection = team.resolve_session_selection(
                session_profile=getattr(session, "agent_profile", None),
                session_team_id=getattr(session, "agent_team_id", None),
            )
            current_profile = current_selection.get("agent_profile")
            current_team_id = current_selection.get("agent_team_id")
            if not current_profile:
                current_profile = getattr(session, "agent_profile", None) or active_profile
            current_profile = str(current_profile or "").strip() or None
            if not current_profile:
                raise HTTPException(status_code=400, detail="No active agent profile available")
            leader_role_for_turn = coordinator.resolve_leader_role(current_team_id, fallback_role=current_profile)

            private_context = chat_repository.get_agent_private_context(session.id, current_profile) or {}
            private_summary = str(private_context.get("context_summary") or "")
            private_after_step_id = private_context.get("last_compressed_step_id")

            updated_summary, updated_call_id, updated_message_id, did_global_compress = await maybe_compress_context(
                session_id=session.id,
                config=config,
                app_config=app_config,
                llm_client=llm_client,
                current_summary=context_summary,
                last_compressed_call_id=last_compressed_call_id,
                current_user_message_id=user_msg.id,
                current_user_text=processed_message,
            )
            if did_global_compress:
                context_summary = updated_summary
                last_compressed_call_id = updated_call_id
                last_compressed_message_id = updated_message_id
                try:
                    chat_repository.update_session_context(session.id, context_summary, last_compressed_call_id)
                except Exception as exc:
                    print(f"[Context Compress] Failed to update session context: {exc}")

            private_summary, private_after_step_id, did_private_compress = await maybe_compress_private_context(
                session_id=session.id,
                agent_profile=current_profile,
                config=config,
                app_config=app_config,
                llm_client=llm_client,
                current_summary=private_summary,
                last_compressed_step_id=private_after_step_id,
                current_user_text=processed_message,
            )

            tool_context = {
                "app_config": app_config,
                "session_id": session.id,
                "agent_profile": current_profile,
                "current_agent_profile": current_profile,
                "agent_team_id": current_team_id,
                "current_agent_team_id": current_team_id,
            }
            pty_prompt = build_live_pty_prompt(session.id)
            system_prompt, tools, resolved_profile_id, ability_ids = build_agent_prompt_and_tools(
                current_profile,
                ToolRegistry.get_all(tool_context),
                include_tools=include_tools,
                extra_context={
                    "pty_sessions": pty_prompt,
                    "active_team_id": current_team_id,
                },
                exclude_ability_ids=["code_map"],
            )
            current_profile = resolved_profile_id or current_profile
            system_prompt = append_reasoning_summary_prompt(system_prompt, global_reasoning_summary)

            if (
                current_profile != getattr(session, "agent_profile", None)
                or current_team_id != getattr(session, "agent_team_id", None)
            ):
                session = session_repository.update_session(
                    session.id,
                    ChatSessionUpdate(agent_profile=current_profile, agent_team_id=current_team_id),
                ) or session

            code_map_prompt = None
            if "code_map" in ability_ids and ast_enabled and code_map_enabled:
                code_map_prompt = build_code_map_prompt(
                    session.id,
                    request.work_path or getattr(session, "work_path", None),
                )

            prompt_truncation_cfg = _build_prompt_truncation_config(context_config)
            history_for_llm = build_history_for_llm(
                session.id,
                last_compressed_message_id,
                user_msg.id,
                context_summary,
                code_map_prompt,
                prompt_truncation_cfg,
                current_agent_profile=current_profile,
                private_summary=private_summary,
                private_after_step_id=private_after_step_id,
            )

            executor = create_agent_executor(
                agent_type=agent_type,
                llm_client=llm_client,
                tools=tools,
                max_iterations=react_max_iterations,
                system_prompt=system_prompt,
            )

            request_overrides: Dict[str, Any] = {
                "_debug": {
                    "session_id": session.id,
                    "message_id": assistant_msg_id,
                    "agent_profile": current_profile,
                },
                "app_config": app_config,
                "current_agent_profile": current_profile,
                "current_agent_team_id": current_team_id,
                "work_path": request.work_path or getattr(session, "work_path", None),
                "prompt_truncation": prompt_truncation_cfg,
                "_stop_event": stop_event,
                "_context_state": {
                    "summary": context_summary,
                    "last_call_id": last_compressed_call_id,
                    "last_message_id": last_compressed_message_id,
                    "current_user_message_id": user_msg.id,
                    "current_agent_profile": current_profile,
                    "private_summary": private_summary,
                    "private_after_step_id": private_after_step_id,
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
            if post_user_messages:
                request_overrides["_post_user_messages"] = list(post_user_messages)

            compress_step = _build_context_step(did_global_compress, did_private_compress)
            if compress_step:
                chat_repository.save_agent_step(
                    message_id=assistant_msg_id,
                    step_type=compress_step.step_type,
                    content=compress_step.content,
                    sequence=sequence,
                    metadata=compress_step.metadata,
                    agent_profile=current_profile,
                )
                await state.emit(compress_step.to_dict())
                sequence += 1

            step_iter = executor.run(
                user_input=processed_message,
                history=history_for_llm,
                session_id=session.id,
                request_overrides=request_overrides,
            )
            step_queue: asyncio.Queue = asyncio.Queue()
            saw_delta = False
            handoff_request = None
            retry_current_agent = False

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
                        agent_profile=current_profile,
                    )

                    if step.step_type == "action" and isinstance(step.metadata, dict) and "tool" in step.metadata:
                        chat_repository.save_tool_call(
                            message_id=assistant_msg_id,
                            tool_name=step.metadata["tool"],
                            tool_input=step.metadata.get("input", ""),
                            tool_output="",
                            agent_profile=current_profile,
                        )

                    if step.step_type == "answer":
                        if (
                            coordinator.is_multi_session_enabled()
                            and leader_role_for_turn
                            and current_profile == leader_role_for_turn
                            and _looks_like_handoff_transcript(step.content)
                        ):
                            guidance = _build_invalid_leader_answer_guidance(current_profile)
                            post_user_messages = list(post_user_messages) if post_user_messages else []
                            post_user_messages.append({"role": "user", "content": guidance})
                            validation_step = AgentStep(
                                step_type="observation",
                                content=guidance,
                                metadata={"runtime_validation": "leader_handoff_answer_blocked"},
                            )
                            chat_repository.save_agent_step(
                                message_id=assistant_msg_id,
                                step_type=validation_step.step_type,
                                content=validation_step.content,
                                sequence=sequence,
                                metadata=validation_step.metadata,
                                agent_profile=current_profile,
                            )
                            await state.emit(validation_step.to_dict())
                            sequence += 1
                            retry_current_agent = True
                            continue

                        final_answer = step.content
                        final_agent_profile = current_profile
                        if not saw_delta:
                            for chunk in _stream_text_chunks(step.content, chunk_size=1):
                                await state.emit({"step_type": "answer_delta", "content": chunk, "metadata": step.metadata})
                        await state.emit(step.to_dict())
                        sequence += 1
                        continue

                    if step.step_type == "error":
                        final_answer = step.content
                        final_agent_profile = current_profile

                    await state.emit(step.to_dict())
                    sequence += 1

                    handoff_request = _extract_handoff_request(step)
                    if handoff_request:
                        break
            finally:
                if producer_task and not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except Exception:
                        pass

            if retry_current_agent:
                session = session_repository.get_session(session.id) or session
                continue

            if handoff_request:
                handoff_count += 1
                if handoff_count > max_handoffs:
                    final_answer = "Too many agent handoffs in a single request."
                    final_agent_profile = current_profile
                    break

                if coordinator.is_multi_session_enabled():
                    await coordinator.execute_delegated_turn(
                        source_session_id=session.id,
                        from_agent=current_profile,
                        to_agent=handoff_request["target_agent"],
                        reason=handoff_request["reason"],
                        work_summary=handoff_request.get("work_summary") or "",
                        task_payload=processed_message,
                        inline_session_message_callback=state.emit,
                        inline_session_ids={session.id},
                    )
                    was_stop_requested = bool(stop_event.is_set()) if stop_event else False
                    if assistant_msg_id:
                        new_assistant_msg_id = await _switch_streaming_assistant_message(
                            state,
                            session.id,
                            int(assistant_msg_id),
                            active_agent_profile=current_profile,
                        )
                        stream_stop_registry.clear(int(assistant_msg_id))
                        assistant_msg_id = new_assistant_msg_id
                        stop_event = stream_stop_registry.create(int(assistant_msg_id))
                        if was_stop_requested:
                            stop_event.set()
                        sequence = 0
                    session = session_repository.get_session(session.id) or session
                    post_user_messages = list(post_user_messages) if post_user_messages else None
                    continue

                payload = await team.apply_handoff(
                    session_id=session.id,
                    from_agent=current_profile,
                    to_agent=handoff_request["target_agent"],
                    reason=handoff_request["reason"],
                    active_team_id=current_team_id,
                )
                await state.emit(payload)
                session = session_repository.get_session(session.id) or session
                post_user_messages = list(post_user_messages) if post_user_messages else None
                continue

            if final_answer is not None:
                break

            final_answer = "Agent completed without a final answer."
            final_agent_profile = current_profile
            break

        if assistant_msg_id:
            final_text = final_answer or ""
            final_metadata = {"agent_profile": final_agent_profile} if final_agent_profile else None
            chat_repository.update_message_content_and_metadata(
                session.id,
                int(assistant_msg_id),
                final_text,
                final_metadata,
            )
            if final_text:
                await maybe_update_session_title(
                    session_id=session.id,
                    config=config,
                    user_message=processed_message,
                    assistant_message=final_text,
                    is_first_turn=is_first_turn,
                    assistant_message_id=assistant_msg_id,
                )

        await state.emit(
            {
                "done": True,
                "session_id": session.id,
                "active_agent_profile": getattr(session, "agent_profile", None),
                "agent_team_id": getattr(session, "agent_team_id", None),
            }
        )
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
