import asyncio
from typing import Dict, Any, Optional, Tuple, List
import traceback

from app_config import get_app_config
from database import db
from models import ChatSessionCreate, ChatSessionUpdate, ChatMessageCreate
from llm_client import create_llm_client
from agents.executor import create_agent_executor
from agents.prompt_builder import build_agent_prompt_and_tools
from tools.base import ToolRegistry
from message_processor import message_processor
from context_compress import build_history_for_llm
from code_map import build_code_map_prompt
from ws_hub import get_ws_hub
from skills import get_enabled_skills, extract_skill_invocations, build_skill_prompt_sections


def _append_reasoning_summary_prompt(system_prompt: str, reasoning_summary: Optional[str]) -> str:
    if not reasoning_summary:
        return system_prompt
    summary = str(reasoning_summary).strip().lower()
    if summary == "concise":
        instruction = "If you include reasoning summaries, keep them concise (1-3 short bullets)."
    elif summary == "detailed":
        instruction = "If you include reasoning summaries, make them detailed and step-by-step; keep final answers concise."
    else:
        instruction = "Provide a reasoning summary only when helpful; otherwise answer directly."
    block = f"## Reasoning Summary\n{instruction}"
    if not system_prompt:
        return block
    return f"{system_prompt}\n\n{block}"


def _normalize_profile_id(value: Any) -> str:
    return str(value or "").strip()


def _iter_profiles(agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    profiles = agent_cfg.get("profiles") or []
    return [profile for profile in profiles if isinstance(profile, dict)]


def _format_profile_label(profile: Dict[str, Any]) -> str:
    profile_id = _normalize_profile_id(profile.get("id"))
    name = str(profile.get("name") or "").strip()
    if profile_id and name and name != profile_id:
        return f"{profile_id} ({name})"
    return profile_id or name or "unknown"


def _is_profile_spawnable(profile: Dict[str, Any], legacy_profile_id: str) -> bool:
    if not isinstance(profile, dict):
        return False
    value = profile.get("spawnable")
    if isinstance(value, bool):
        return value
    profile_id = _normalize_profile_id(profile.get("id"))
    return bool(legacy_profile_id) and profile_id == legacy_profile_id


def list_spawnable_profiles(agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    profiles = _iter_profiles(agent_cfg)
    legacy_profile_id = _normalize_profile_id(agent_cfg.get("subagent_profile"))
    return [profile for profile in profiles if _is_profile_spawnable(profile, legacy_profile_id)]


def _resolve_subagent_profile(
    agent_cfg: Dict[str, Any],
    desired_profile_id: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    profiles = _iter_profiles(agent_cfg)
    legacy_profile_id = _normalize_profile_id(agent_cfg.get("subagent_profile"))
    spawnable_profiles = [profile for profile in profiles if _is_profile_spawnable(profile, legacy_profile_id)]
    desired = _normalize_profile_id(desired_profile_id)

    if desired:
        selected = next(
            (profile for profile in profiles if _normalize_profile_id(profile.get("id")) == desired),
            None
        )
        if not selected:
            choices = ", ".join([_format_profile_label(profile) for profile in spawnable_profiles])
            raise ValueError(
                f"Subagent profile not found: {desired}"
                + (f". Spawnable profiles: {choices}" if choices else "")
            )
        if not _is_profile_spawnable(selected, legacy_profile_id):
            choices = ", ".join([_format_profile_label(profile) for profile in spawnable_profiles])
            raise ValueError(
                f"Profile not spawnable: {desired}"
                + (f". Spawnable profiles: {choices}" if choices else "")
            )
        return desired, selected

    if len(spawnable_profiles) == 1:
        selected = spawnable_profiles[0]
        profile_id = _normalize_profile_id(selected.get("id")) or legacy_profile_id or "subagent"
        return profile_id, selected

    if not spawnable_profiles:
        raise ValueError("No spawnable profiles configured. Mark a profile as spawnable.")

    choices = ", ".join([_format_profile_label(profile) for profile in spawnable_profiles])
    raise ValueError(f"Multiple spawnable profiles available: {choices}. Please specify profile_id.")


def prepare_subagent_session(
    task: str,
    parent_session_id: str,
    title: Optional[str] = None,
    profile_id: Optional[str] = None
) -> Dict[str, Any]:
    if not task or not str(task).strip():
        raise ValueError("Missing task")

    parent = db.get_session(parent_session_id)
    if not parent:
        raise ValueError("Parent session not found")

    config = db.get_config(parent.config_id)
    if not config:
        raise ValueError("Parent config not found")

    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    subagent_profile_id, subagent_profile = _resolve_subagent_profile(agent_cfg, profile_id)

    child_title = title.strip() if isinstance(title, str) and title.strip() else "Subagent Task"
    child_session = db.create_session(ChatSessionCreate(
        title=child_title,
        config_id=parent.config_id,
        work_path=getattr(parent, "work_path", None),
        agent_profile=subagent_profile_id,
        parent_session_id=parent_session_id
    ))

    processed_message = message_processor.preprocess_user_message(str(task))
    user_msg = db.create_message(ChatMessageCreate(
        session_id=child_session.id,
        role="user",
        content=processed_message
    ))

    return {
        "parent_session_id": parent_session_id,
        "parent_config_id": parent.config_id,
        "child_session": child_session,
        "child_session_id": child_session.id,
        "child_title": child_title,
        "processed_message": processed_message,
        "user_message_id": user_msg.id,
        "assistant_message_id": None,
        "subagent_profile_id": subagent_profile_id
    }


async def _notify_parent_subagent_done(
    parent_session_id: str,
    child_session_id: str,
    child_title: str,
    final_answer: str,
    status: str
) -> None:
    if not parent_session_id:
        return
    status_label = "完成" if status == "ok" else "失败"
    content = f"子agent「{child_title}」{status_label}：\n\n{final_answer}"
    metadata = {
        "subagent_done": True,
        "child_session_id": child_session_id,
        "child_title": child_title,
        "status": status
    }
    message = db.create_message(ChatMessageCreate(
        session_id=parent_session_id,
        role="assistant",
        content=content,
        metadata=metadata
    ))

    try:
        hub = get_ws_hub()
        payload = {
            "type": "subagent_done",
            "session_id": parent_session_id,
            "message": message.dict(),
            "child_session_id": child_session_id,
            "status": status
        }
        await hub.emit(parent_session_id, payload)
    except Exception as exc:
        print(f"[Subagent] Failed to emit parent notification: {exc}")


async def execute_subagent_context(context: Dict[str, Any]) -> Dict[str, Any]:
    child_session = context.get("child_session") or db.get_session(context["child_session_id"])
    if not child_session:
        final_answer = "Subagent failed: Child session not found"
        assistant_msg_id = context.get("assistant_message_id")
        if assistant_msg_id:
            try:
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    UPDATE chat_messages
                    SET content = ?
                    WHERE id = ?
                    ''',
                    (final_answer, assistant_msg_id)
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        await _notify_parent_subagent_done(
            parent_session_id=context.get("parent_session_id", ""),
            child_session_id=context.get("child_session_id", ""),
            child_title=context.get("child_title", "Subagent Task"),
            final_answer=final_answer,
            status="error"
        )
        return {"status": "error", "error": "Child session not found"}

    assistant_msg_id = context.get("assistant_message_id")
    user_msg_id = context["user_message_id"]
    processed_message = context["processed_message"]
    child_title = context["child_title"]
    parent_session_id = context["parent_session_id"]
    subagent_profile_id = context["subagent_profile_id"]

    config = db.get_config(child_session.config_id)
    if not config:
        final_answer = "Subagent failed: Parent config not found"
        if assistant_msg_id:
            try:
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    UPDATE chat_messages
                    SET content = ?
                    WHERE id = ?
                    ''',
                    (final_answer, assistant_msg_id)
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        else:
            try:
                db.create_message(ChatMessageCreate(
                    session_id=child_session.id,
                    role="assistant",
                    content=final_answer
                ))
            except Exception:
                pass
        await _notify_parent_subagent_done(
            parent_session_id=parent_session_id,
            child_session_id=child_session.id,
            child_title=child_title,
            final_answer=final_answer,
            status="error"
        )
        return {"status": "error", "error": "Parent config not found"}

    app_config = get_app_config()
    llm_app_config = app_config.get("llm", {}) if isinstance(app_config, dict) else {}
    global_reasoning_summary = llm_app_config.get("reasoning_summary")
    if global_reasoning_summary:
        try:
            config.reasoning_summary = str(global_reasoning_summary)
        except Exception:
            pass

    context_config = app_config.get("context", {}) if isinstance(app_config, dict) else {}
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    agent_config = agent_cfg if isinstance(agent_cfg, dict) else {}
    ast_enabled = bool(agent_config.get("ast_enabled", True))
    code_map_cfg = agent_config.get("code_map", {}) if isinstance(agent_config, dict) else {}
    code_map_enabled = bool(code_map_cfg.get("enabled", True))

    pty_prompt = "None."
    system_prompt, tools, resolved_profile_id, ability_ids = build_agent_prompt_and_tools(
        subagent_profile_id,
        ToolRegistry.get_all(),
        include_tools=True,
        extra_context={"pty_sessions": pty_prompt},
        exclude_ability_ids=["code_map"]
    )
    enabled_skills = get_enabled_skills()
    invoked_names = extract_skill_invocations(processed_message, max_count=1)
    invoked_skills = []
    if enabled_skills and invoked_names:
        skill_map = {skill.name.lower(): skill for skill in enabled_skills}
        for name in invoked_names:
            skill = skill_map.get(name.lower())
            if skill:
                invoked_skills.append(skill)
    skills_prompt = build_skill_prompt_sections([], invoked_skills)
    system_prompt = _append_reasoning_summary_prompt(system_prompt, global_reasoning_summary)
    if resolved_profile_id and resolved_profile_id != getattr(child_session, "agent_profile", None):
        db.update_session(child_session.id, ChatSessionUpdate(agent_profile=resolved_profile_id))

    code_map_prompt = None
    if "code_map" in ability_ids and ast_enabled and code_map_enabled:
        code_map_prompt = build_code_map_prompt(
            child_session.id,
            getattr(child_session, "work_path", None)
        )

    react_max_iterations = agent_config.get("react_max_iterations", 50)
    try:
        react_max_iterations = int(react_max_iterations)
    except (TypeError, ValueError):
        react_max_iterations = 50

    llm_client = create_llm_client(config)
    executor = create_agent_executor(
        agent_type="react",
        llm_client=llm_client,
        tools=tools,
        max_iterations=react_max_iterations,
        system_prompt=system_prompt
    )

    prompt_truncation_cfg = {
        "enabled": bool(context_config.get("truncate_long_data", True)),
        "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
        "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
        "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800)
    }

    history_for_llm = build_history_for_llm(
        child_session.id,
        None,
        user_msg_id,
        "",
        code_map_prompt,
        prompt_truncation_cfg
    )
    if skills_prompt:
        skill_meta = {"skill_prompt": True}
        if invoked_skills:
            skill_meta["skill_name"] = invoked_skills[0].name
            skill_meta["skill_path"] = invoked_skills[0].path
        db.create_message(ChatMessageCreate(
            session_id=child_session.id,
            role="user",
            content=skills_prompt,
            metadata=skill_meta
        ))

    if not assistant_msg_id:
        temp_assistant_msg = db.create_message(ChatMessageCreate(
            session_id=child_session.id,
            role="assistant",
            content=""
        ))
        assistant_msg_id = temp_assistant_msg.id

    request_overrides: Dict[str, Any] = {
        "_debug": {"session_id": child_session.id, "message_id": assistant_msg_id},
        "work_path": getattr(child_session, "work_path", None),
        "prompt_truncation": prompt_truncation_cfg,
        "_context_state": {
            "summary": "",
            "last_call_id": None,
            "last_message_id": None,
            "current_user_message_id": user_msg_id
        }
    }
    if code_map_prompt:
        request_overrides["_code_map_prompt"] = code_map_prompt
    if skills_prompt:
        request_overrides["_post_user_messages"] = [{"role": "user", "content": skills_prompt}]

    sequence = 0
    final_answer = None
    had_error = False

    try:
        async for step in executor.run(
            user_input=processed_message,
            history=history_for_llm,
            session_id=child_session.id,
            request_overrides=request_overrides
        ):
            if step.step_type == "context_estimate":
                try:
                    db.update_session_context_estimate(child_session.id, step.metadata)
                except Exception:
                    pass
                continue

            if step.step_type.endswith("_delta"):
                continue

            suppress_prompt = False
            if step.step_type == "error":
                suppress_prompt = bool(step.metadata.get("suppress_prompt")) if isinstance(step.metadata, dict) else False

            if not suppress_prompt:
                db.save_agent_step(
                    message_id=assistant_msg_id,
                    step_type=step.step_type,
                    content=step.content,
                    sequence=sequence,
                    metadata=step.metadata
                )
                if step.step_type == "action" and isinstance(step.metadata, dict) and "tool" in step.metadata:
                    db.save_tool_call(
                        message_id=assistant_msg_id,
                        tool_name=step.metadata["tool"],
                        tool_input=step.metadata.get("input", ""),
                        tool_output=""
                    )
                sequence += 1

            if step.step_type == "answer":
                final_answer = step.content
                break
            if step.step_type == "error":
                final_answer = step.content
                had_error = True

    except asyncio.CancelledError:
        had_error = True
        final_answer = "Subagent cancelled."
        try:
            db.save_agent_step(
                message_id=assistant_msg_id,
                step_type="error",
                content=final_answer,
                sequence=sequence,
                metadata={"error": "cancelled", "cancelled": True}
            )
        except Exception:
            pass
    except Exception as exc:
        had_error = True
        final_answer = f"Subagent failed: {exc}"
        db.save_agent_step(
            message_id=assistant_msg_id,
            step_type="error",
            content=final_answer,
            sequence=sequence,
            metadata={"error": str(exc), "traceback": traceback.format_exc()}
        )

    if final_answer is None:
        final_answer = ""

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''
        UPDATE chat_messages
        SET content = ?
        WHERE id = ?
        ''',
        (final_answer, assistant_msg_id)
    )
    conn.commit()
    conn.close()

    status = "error" if had_error else "ok"
    await _notify_parent_subagent_done(
        parent_session_id=parent_session_id,
        child_session_id=child_session.id,
        child_title=child_title,
        final_answer=final_answer,
        status=status
    )

    return {
        "status": status,
        "child_session_id": child_session.id,
        "result": final_answer,
        "title": child_title
    }


async def run_subagent_task(
    task: str,
    parent_session_id: str,
    title: Optional[str] = None,
    profile_id: Optional[str] = None
) -> Dict[str, Any]:
    try:
        context = prepare_subagent_session(task, parent_session_id, title, profile_id)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    return await execute_subagent_context(context)
