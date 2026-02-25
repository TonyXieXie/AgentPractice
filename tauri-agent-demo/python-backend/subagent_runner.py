from typing import Dict, Any, Optional, Tuple
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


def _resolve_subagent_profile(agent_cfg: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    profile_id = str(agent_cfg.get("subagent_profile") or "subagent").strip() or "subagent"
    profiles = agent_cfg.get("profiles") or []
    for profile in profiles:
        if isinstance(profile, dict) and str(profile.get("id") or "") == profile_id:
            return profile_id, profile
    return profile_id, None


def _is_profile_spawnable(profile: Dict[str, Any], subagent_profile_id: str) -> bool:
    if not isinstance(profile, dict):
        return False
    value = profile.get("spawnable")
    if isinstance(value, bool):
        return value
    return str(profile.get("id") or "") == subagent_profile_id


async def run_subagent_task(
    task: str,
    parent_session_id: str,
    title: Optional[str] = None
) -> Dict[str, Any]:
    if not task or not str(task).strip():
        return {"status": "error", "error": "Missing task"}

    parent = db.get_session(parent_session_id)
    if not parent:
        return {"status": "error", "error": "Parent session not found"}

    config = db.get_config(parent.config_id)
    if not config:
        return {"status": "error", "error": "Parent config not found"}

    app_config = get_app_config()
    agent_cfg = app_config.get("agent", {}) if isinstance(app_config, dict) else {}
    subagent_profile_id, subagent_profile = _resolve_subagent_profile(agent_cfg)
    if not subagent_profile:
        return {
            "status": "error",
            "error": f"Subagent profile not found: {subagent_profile_id}"
        }
    if not _is_profile_spawnable(subagent_profile, subagent_profile_id):
        return {
            "status": "error",
            "error": f"Profile not spawnable: {subagent_profile_id}"
        }

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
    assistant_msg = db.create_message(ChatMessageCreate(
        session_id=child_session.id,
        role="assistant",
        content=""
    ))
    assistant_msg_id = assistant_msg.id

    llm_app_config = app_config.get("llm", {}) if isinstance(app_config, dict) else {}
    global_reasoning_summary = llm_app_config.get("reasoning_summary")
    if global_reasoning_summary:
        try:
            config.reasoning_summary = str(global_reasoning_summary)
        except Exception:
            pass

    context_config = app_config.get("context", {}) if isinstance(app_config, dict) else {}
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

    request_overrides: Dict[str, Any] = {
        "_debug": {"session_id": child_session.id, "message_id": assistant_msg_id},
        "work_path": getattr(child_session, "work_path", None),
        "prompt_truncation": {
            "enabled": bool(context_config.get("truncate_long_data", True)),
            "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
            "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
            "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800)
        },
        "_context_state": {
            "summary": "",
            "last_call_id": None,
            "last_message_id": None,
            "current_user_message_id": user_msg.id
        }
    }
    if code_map_prompt:
        request_overrides["_code_map_prompt"] = code_map_prompt

    history_for_llm = build_history_for_llm(
        child_session.id,
        None,
        user_msg.id,
        "",
        code_map_prompt,
        request_overrides.get("prompt_truncation")
    )

    sequence = 0
    final_answer = None

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

    except Exception as exc:
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

    return {
        "status": "ok",
        "child_session_id": child_session.id,
        "result": final_answer,
        "title": child_title
    }
