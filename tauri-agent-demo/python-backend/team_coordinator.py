import asyncio
import inspect
import json
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

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
from ghost_snapshot import create_snapshot_tree, diff_snapshot_trees
from llm_client import create_llm_client
from message_processor import message_processor
from models import ChatMessageCreate, ChatSessionCreate, ChatSessionUpdate, TeamHandoffEvent
from repositories import chat_repository, config_repository, session_repository, team_repository
from tools.base import ToolRegistry
from ws_hub import get_ws_hub

from server.agent_prompt_support import append_reasoning_summary_prompt, build_live_pty_prompt


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_prompt_truncation_config(context_config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "enabled": bool(context_config.get("truncate_long_data", True)),
        "threshold": int(context_config.get("long_data_threshold", 4000) or 4000),
        "head_chars": int(context_config.get("long_data_head_chars", 1200) or 1200),
        "tail_chars": int(context_config.get("long_data_tail_chars", 800) or 800),
    }


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


def _summarize_result(text: Optional[str], max_chars: int = 800) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 15)].rstrip() + "\n...[truncated]"


def _normalize_changed_file_status(value: Any) -> Optional[str]:
    normalized = _normalize_text(value).lower()
    if normalized in {"a", "add", "added"}:
        return "added"
    if normalized in {"d", "delete", "deleted", "remove", "removed"}:
        return "deleted"
    if normalized in {"m", "modify", "modified", "update", "updated"}:
        return "modified"
    return None


def _normalize_artifact_source(value: Any) -> Optional[str]:
    normalized = _normalize_text(value).lower()
    if normalized in {"snapshot_diff", "snapshot"}:
        return "snapshot_diff"
    if normalized in {"tool_calls_fallback", "tool_calls", "tool_call_fallback"}:
        return "tool_calls_fallback"
    return None


def _normalize_changed_files(changed_files: Any) -> List[Dict[str, str]]:
    if not isinstance(changed_files, list):
        return []
    deduped: Dict[str, str] = {}
    for item in changed_files:
        if not isinstance(item, dict):
            continue
        path = _normalize_text(item.get("path"))
        status = _normalize_changed_file_status(item.get("status"))
        if not path or not status:
            continue
        deduped[path] = status
    return [{"path": path, "status": status} for path, status in deduped.items()]


def _build_artifact_summary(changed_files: Any, max_files: int = 10) -> str:
    normalized_files = _normalize_changed_files(changed_files)
    if not normalized_files:
        return ""
    status_codes = {
        "added": "A",
        "modified": "M",
        "deleted": "D",
    }
    visible_files = normalized_files[:max_files]
    parts = [f"{status_codes.get(item['status'], 'M')} {item['path']}" for item in visible_files]
    remaining = len(normalized_files) - len(visible_files)
    if remaining > 0:
        parts.append(f"+ {remaining} more files")
    return "; ".join(parts)


def _append_artifact_summary(content: Optional[str], artifact_summary: Optional[str]) -> str:
    base_text = str(content or "").strip()
    summary_text = _normalize_text(artifact_summary)
    if not summary_text:
        return base_text
    changed_line = f"Changed files: {summary_text}"
    if changed_line in base_text:
        return base_text
    if not base_text:
        return changed_line
    return f"{base_text}\n{changed_line}"


def _parse_tool_input_json(raw_input: Any) -> Dict[str, Any]:
    text = str(raw_input or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_changed_files_from_patch_text(patch_text: Any) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    header_prefixes = {
        "*** Add File: ": "added",
        "*** Update File: ": "modified",
        "*** Delete File: ": "deleted",
    }
    for raw_line in str(patch_text or "").splitlines():
        line = str(raw_line or "").strip()
        for prefix, status in header_prefixes.items():
            if not line.startswith(prefix):
                continue
            path = _normalize_text(line[len(prefix):])
            if path:
                results.append({"path": path, "status": status})
            break
    return results


class TeamCoordinator:
    def __init__(self, app_config: Optional[Dict[str, Any]] = None):
        self.app_config = app_config if isinstance(app_config, dict) else get_app_config()
        self.team = AgentTeam(self.app_config)
        agent_cfg = self.app_config.get("agent", {}) if isinstance(self.app_config, dict) else {}
        legacy_team_cfg = agent_cfg.get("team", {}) if isinstance(agent_cfg, dict) else {}
        execution_mode = str(legacy_team_cfg.get("execution_mode") or "").strip().lower()
        self.execution_mode = execution_mode or "single_session"

    def is_multi_session_enabled(self) -> bool:
        return self.execution_mode == "multi_session"

    def ensure_team(self, session_id: str, current_profile: Optional[str]) -> Tuple[Any, Any]:
        session = session_repository.get_session(session_id, include_count=False)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        role_key = _normalize_text(getattr(session, "role_key", None) or current_profile or getattr(session, "agent_profile", None))
        if not role_key:
            raise HTTPException(status_code=400, detail="No role key available for runtime team")

        runtime_team_id = _normalize_text(getattr(session, "team_id", None))
        if runtime_team_id:
            runtime_team = team_repository.get_team(runtime_team_id)
            updates: Dict[str, Any] = {}
            if not getattr(session, "role_key", None):
                updates["role_key"] = role_key
            if not getattr(session, "agent_profile", None):
                updates["agent_profile"] = role_key
            if updates:
                session = session_repository.update_session(session.id, ChatSessionUpdate(**updates)) or session
            if runtime_team:
                return runtime_team, session

        runtime_team = team_repository.create_team(session.id)
        session = session_repository.update_session(
            session.id,
            ChatSessionUpdate(team_id=runtime_team.id, role_key=role_key, agent_profile=role_key),
        ) or session
        return runtime_team, session

    def _build_role_session_title(self, source_session: Any, role_key: str) -> str:
        profile = self.team.get_profile(role_key) or {}
        profile_name = _normalize_text(profile.get("name") or role_key) or role_key
        base_title = _normalize_text(getattr(source_session, "title", None)) or "Team Session"
        return f"{base_title} [{profile_name}]"

    async def _backfill_team_history_to_session(self, session_id: str, team_id: str) -> None:
        events = team_repository.list_handoff_events(team_id)
        for event in events:
            await self._mirror_event_to_session(session_id, event, emit=False)

    async def resolve_or_create_role_session(
        self,
        team_id: str,
        role_key: str,
        source_session: Any,
        backfill_history: bool = True,
    ) -> Tuple[Any, bool]:
        existing = session_repository.get_session_by_runtime_team_role(team_id, role_key)
        if existing:
            updates: Dict[str, Any] = {}
            if getattr(existing, "agent_profile", None) != role_key:
                updates["agent_profile"] = role_key
            if getattr(existing, "agent_team_id", None) != getattr(source_session, "agent_team_id", None):
                updates["agent_team_id"] = getattr(source_session, "agent_team_id", None)
            if getattr(existing, "team_id", None) != team_id:
                updates["team_id"] = team_id
            if getattr(existing, "role_key", None) != role_key:
                updates["role_key"] = role_key
            if updates:
                existing = session_repository.update_session(existing.id, ChatSessionUpdate(**updates)) or existing
            return existing, False

        created = session_repository.create_session(
            ChatSessionCreate(
                title=self._build_role_session_title(source_session, role_key),
                config_id=source_session.config_id,
                work_path=getattr(source_session, "work_path", None),
                agent_profile=role_key,
                agent_team_id=getattr(source_session, "agent_team_id", None),
                team_id=team_id,
                role_key=role_key,
                parent_session_id=source_session.id,
            )
        )
        if backfill_history:
            await self._backfill_team_history_to_session(created.id, team_id)
        return created, True

    def resolve_leader_role(self, agent_team_id: Optional[str], fallback_role: Optional[str] = None) -> Optional[str]:
        leader_role = _normalize_text(self.team.get_team_leader_id(agent_team_id))
        if leader_role:
            return leader_role
        fallback = _normalize_text(fallback_role)
        return fallback or None

    def append_handoff_event(
        self,
        team_id: str,
        handoff_id: str,
        event_kind: str,
        from_session_id: Optional[str] = None,
        from_role_key: Optional[str] = None,
        to_session_id: Optional[str] = None,
        to_role_key: Optional[str] = None,
        reason: Optional[str] = None,
        work_summary: Optional[str] = None,
        artifact_summary: Optional[str] = None,
        changed_files: Optional[List[Dict[str, str]]] = None,
        artifact_source: Optional[str] = None,
        artifact_owner_session_id: Optional[str] = None,
        artifact_owner_role_key: Optional[str] = None,
        task_payload: Optional[str] = None,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
        parent_handoff_id: Optional[str] = None,
    ) -> TeamHandoffEvent:
        return team_repository.create_handoff_event(
            TeamHandoffEvent(
                team_id=team_id,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                event_kind=str(event_kind),
                from_session_id=from_session_id,
                from_role_key=from_role_key,
                to_session_id=to_session_id,
                to_role_key=to_role_key,
                reason=reason,
                work_summary=work_summary,
                artifact_summary=_normalize_text(artifact_summary) or None,
                changed_files=_normalize_changed_files(changed_files) or None,
                artifact_source=_normalize_artifact_source(artifact_source),
                artifact_owner_session_id=_normalize_text(artifact_owner_session_id) or None,
                artifact_owner_role_key=_normalize_text(artifact_owner_role_key) or None,
                task_payload=task_payload,
                result_summary=result_summary,
                error=error,
            )
        )

    def _format_role_label(self, role_key: Optional[str]) -> str:
        normalized_role = _normalize_text(role_key)
        if not normalized_role:
            return "Unknown"
        profile = self.team.get_profile(normalized_role) or {}
        profile_name = _normalize_text(profile.get("name") or normalized_role)
        return profile_name or normalized_role

    def _find_handoff_mirror_message(self, session_id: str, handoff_id: str) -> Optional[Any]:
        for message in reversed(chat_repository.list_messages(session_id)):
            metadata = getattr(message, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            if metadata.get("event_type") != "handoff":
                continue
            if str(metadata.get("handoff_id") or "").strip() != handoff_id:
                continue
            return message
        return None

    def _build_return_to_leader_report(
        self,
        from_role_key: Optional[str],
        leader_role_key: Optional[str],
        reason: Optional[str],
        work_summary: Optional[str],
    ) -> str:
        from_label = self._format_role_label(from_role_key)
        leader_label = self._format_role_label(leader_role_key)
        lines = [f"[{from_label}] Returned control to [{leader_label}] for leader decision."]
        if work_summary:
            lines.append(f"Work summary: {work_summary}")
        if reason:
            lines.append(f"Why leader is needed: {reason}")
        lines.append("Only the leader may decide whether the overall user task is complete.")
        return "\n".join(lines)

    def _capture_tree_hash(self, work_path: Optional[str]) -> Optional[str]:
        normalized_path = _normalize_text(work_path)
        if not normalized_path:
            return None
        try:
            return create_snapshot_tree(normalized_path)
        except Exception:
            return None

    def _extract_changed_files_from_tool_calls(self, assistant_message_id: Optional[int]) -> List[Dict[str, str]]:
        if not assistant_message_id:
            return []
        changed_files: List[Dict[str, str]] = []
        for tool_call in chat_repository.get_tool_calls_for_message(int(assistant_message_id)):
            tool_name = _normalize_text(tool_call.get("tool_name")).lower()
            tool_input = tool_call.get("tool_input")
            if tool_name == "write_file":
                payload = _parse_tool_input_json(tool_input)
                path = _normalize_text(payload.get("path") or tool_input)
                if path and "\n" not in path:
                    changed_files.append({"path": path, "status": "modified"})
            elif tool_name == "apply_patch":
                payload = _parse_tool_input_json(tool_input)
                patch_text = payload.get("patch") if payload else tool_input
                changed_files.extend(_extract_changed_files_from_patch_text(patch_text))
        return _normalize_changed_files(changed_files)

    def _collect_turn_artifacts(
        self,
        work_path: Optional[str],
        baseline_tree_hash: Optional[str],
        assistant_message_id: Optional[int],
    ) -> Tuple[str, List[Dict[str, str]], Optional[str]]:
        changed_files: List[Dict[str, str]] = []
        artifact_source: Optional[str] = None
        normalized_path = _normalize_text(work_path)
        if normalized_path and baseline_tree_hash:
            try:
                end_tree_hash = create_snapshot_tree(normalized_path)
                changed_files = _normalize_changed_files(
                    diff_snapshot_trees(normalized_path, baseline_tree_hash, end_tree_hash)
                )
                if changed_files:
                    artifact_source = "snapshot_diff"
            except Exception:
                changed_files = []
        if not changed_files:
            changed_files = self._extract_changed_files_from_tool_calls(assistant_message_id)
            if changed_files:
                artifact_source = "tool_calls_fallback"
        artifact_summary = _build_artifact_summary(changed_files)
        return artifact_summary, changed_files, artifact_source

    def _prefix_role_report(
        self,
        role_key: Optional[str],
        content: Optional[str],
        fallback: Optional[str] = None,
    ) -> str:
        label = self._format_role_label(role_key)
        normalized_content = _normalize_text(content)
        if normalized_content.startswith(f"[{label}]"):
            return normalized_content
        if not normalized_content:
            normalized_content = _normalize_text(fallback)
        if not normalized_content:
            return f"[{label}]"
        return f"[{label}]: {normalized_content}"

    def _build_event_message(self, event: TeamHandoffEvent) -> Tuple[str, Dict[str, Any]]:
        from_role = _normalize_text(event.from_role_key) or "unknown"
        to_role = _normalize_text(event.to_role_key) or "unknown"
        from_label = self._format_role_label(from_role)
        to_label = self._format_role_label(to_role)
        if event.work_summary:
            first_line = self._prefix_role_report(
                from_role,
                event.work_summary,
                fallback=f"Handing work to [{to_label}].",
            )
        elif event.reason:
            first_line = self._prefix_role_report(
                from_role,
                f"Handing work to [{to_label}] for {event.reason}",
            )
        else:
            first_line = self._prefix_role_report(from_role, f"Handing work to [{to_label}].")

        lines = [first_line]
        if event.reason:
            lines.append(f"Assigned to [{to_label}]: {event.reason}")
        artifact_summary = _normalize_text(getattr(event, "artifact_summary", None))
        artifact_owner_role = _normalize_text(getattr(event, "artifact_owner_role_key", None))
        artifact_owner_label = self._format_role_label(artifact_owner_role) if artifact_owner_role else ""
        if artifact_summary and artifact_owner_label and artifact_owner_role != from_role:
            lines.append(f"Artifacts from [{artifact_owner_label}].")
        if artifact_summary:
            lines.append(f"Changed files: {artifact_summary}")
        if event.event_kind == "failed":
            lines.append(f"Handoff error: {event.error}" if event.error else "Handoff error: unknown failure.")

        content = "\n".join(lines)

        metadata: Dict[str, Any] = {
            "event_type": "handoff",
            "team_id": event.team_id,
            "handoff_id": event.handoff_id,
            "team_handoff_event_id": event.id,
            "event_kind": event.event_kind,
            "from_agent": event.from_role_key,
            "to_agent": event.to_role_key,
            "from_session_id": event.from_session_id,
            "to_session_id": event.to_session_id,
            "parent_handoff_id": event.parent_handoff_id,
            "reason": event.reason,
            "from_agent_label": from_label,
            "to_agent_label": to_label,
        }
        if event.work_summary:
            metadata["work_summary"] = event.work_summary
        if artifact_summary:
            metadata["artifact_summary"] = artifact_summary
        normalized_changed_files = _normalize_changed_files(getattr(event, "changed_files", None))
        if normalized_changed_files:
            metadata["changed_files"] = normalized_changed_files
        artifact_source = _normalize_artifact_source(getattr(event, "artifact_source", None))
        if artifact_source:
            metadata["artifact_source"] = artifact_source
        artifact_owner_session_id = _normalize_text(getattr(event, "artifact_owner_session_id", None))
        if artifact_owner_session_id:
            metadata["artifact_owner_session_id"] = artifact_owner_session_id
        if artifact_owner_role:
            metadata["artifact_owner_role_key"] = artifact_owner_role
            metadata["artifact_owner_label"] = artifact_owner_label
        if event.task_payload:
            metadata["task_payload"] = event.task_payload
        if event.result_summary:
            metadata["result_summary"] = event.result_summary
        if event.error:
            metadata["error"] = event.error
        return content, metadata

    async def append_delegated_result_message(
        self,
        session_id: str,
        team_id: str,
        handoff_id: str,
        from_role_key: str,
        to_role_key: str,
        source_session_id: str,
        target_session_id: str,
        content: str,
        status: str = "ok",
        artifact_summary: Optional[str] = None,
        changed_files: Optional[List[Dict[str, str]]] = None,
        artifact_source: Optional[str] = None,
        artifact_owner_session_id: Optional[str] = None,
        artifact_owner_role_key: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Any:
        normalized_content = str(content or "").strip()
        if not normalized_content:
            normalized_content = "Delegated agent returned no result."
        if status != "ok":
            normalized_content = f"Encountered an error while handling the task.\n{normalized_content}"
        normalized_artifact_summary = _normalize_text(artifact_summary)
        normalized_changed_files = _normalize_changed_files(changed_files)
        normalized_artifact_source = _normalize_artifact_source(artifact_source)
        normalized_artifact_owner_session_id = _normalize_text(artifact_owner_session_id) or None
        normalized_artifact_owner_role_key = _normalize_text(artifact_owner_role_key) or None
        if not normalized_artifact_summary:
            normalized_artifact_summary = _build_artifact_summary(normalized_changed_files)
        normalized_content = _append_artifact_summary(normalized_content, normalized_artifact_summary)
        artifact_owner_label = (
            self._format_role_label(normalized_artifact_owner_role_key)
            if normalized_artifact_owner_role_key
            else ""
        )
        rendered_content = self._prefix_role_report(
            to_role_key,
            normalized_content,
            fallback="Delegated agent returned no result.",
        )
        message = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session_id,
                role="assistant",
                content=rendered_content,
                metadata={
                    "event_type": "delegated_result",
                    "team_id": team_id,
                    "handoff_id": handoff_id,
                    "from_role_key": from_role_key,
                    "to_role_key": to_role_key,
                    "source_session_id": source_session_id,
                    "target_session_id": target_session_id,
                    "status": status,
                    "artifact_summary": normalized_artifact_summary or None,
                    "changed_files": normalized_changed_files or None,
                    "artifact_source": normalized_artifact_source,
                    "artifact_owner_session_id": normalized_artifact_owner_session_id,
                    "artifact_owner_role_key": normalized_artifact_owner_role_key,
                    "artifact_owner_label": artifact_owner_label or None,
                },
            )
        )
        await self._emit_session_message(
            session_id,
            message,
            inline_session_message_callback=inline_session_message_callback,
            inline_session_ids=inline_session_ids,
        )
        return message

    def _build_delegated_user_content(
        self,
        source_role: str,
        target_role: str,
        leader_role: str,
        reason: str,
        work_summary: str,
        task_payload: str,
    ) -> str:
        return message_processor.preprocess_user_message(
            self._build_delegated_user_message(source_role, target_role, leader_role, reason, work_summary, task_payload)
        )

    async def _create_delegated_user_message(
        self,
        session_id: str,
        source_role: str,
        target_role: str,
        leader_role: str,
        reason: str,
        work_summary: str,
        task_payload: str,
        handoff_id: str,
        parent_handoff_id: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Tuple[int, str]:
        processed_message = self._build_delegated_user_content(
            source_role,
            target_role,
            leader_role,
            reason,
            work_summary,
            task_payload,
        )
        user_msg = chat_repository.create_message(
            ChatMessageCreate(
                session_id=session_id,
                role="user",
                content=processed_message,
                metadata={
                    "delegated_turn": True,
                    "handoff_id": handoff_id,
                    "parent_handoff_id": parent_handoff_id,
                    "source_role": source_role,
                    "target_role": target_role,
                    "reason": reason,
                    "work_summary": work_summary,
                    "leader_role": leader_role,
                    "task_payload": task_payload,
                },
            )
        )
        await self._emit_session_message(
            session_id,
            user_msg,
            active_agent_profile=target_role,
            inline_session_message_callback=inline_session_message_callback,
            inline_session_ids=inline_session_ids,
        )
        return int(user_msg.id), processed_message

    def _message_to_payload_dict(self, message: Any) -> Dict[str, Any]:
        if isinstance(message, dict):
            return dict(message)
        if hasattr(message, "model_dump"):
            return message.model_dump()
        if hasattr(message, "dict"):
            return message.dict()
        return dict(message or {})

    async def _emit_session_message(
        self,
        session_id: str,
        message: Any,
        active_agent_profile: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> None:
        payload = {
            "type": "session_message",
            "session_id": session_id,
            "message": self._message_to_payload_dict(message),
        }
        if active_agent_profile:
            payload["active_agent_profile"] = active_agent_profile
        try:
            await get_ws_hub().emit(session_id, payload)
        except Exception:
            pass
        if inline_session_message_callback and (not inline_session_ids or session_id in inline_session_ids):
            try:
                await inline_session_message_callback(payload)
            except Exception:
                pass

    def _build_live_assistant_snapshot(
        self,
        assistant_message: Any,
        live_steps: Any,
        current_profile: str,
        content: Optional[str] = None,
        streaming: bool = True,
    ) -> Dict[str, Any]:
        payload = self._message_to_payload_dict(assistant_message)
        payload["content"] = content if content is not None else str(payload.get("content") or "")
        payload["metadata"] = {
            **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
            "agent_profile": current_profile,
            "agent_steps": list(live_steps or []),
            "agent_streaming": streaming,
        }
        return payload

    async def _mirror_event_to_session(
        self,
        session_id: str,
        event: TeamHandoffEvent,
        emit: bool = True,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Any:
        content, metadata = self._build_event_message(event)
        existing_message = self._find_handoff_mirror_message(session_id, event.handoff_id)
        if existing_message:
            message_id = int(existing_message.id)
            chat_repository.update_message_content_and_metadata(session_id, message_id, content, metadata)
            message = chat_repository.get_message_details(session_id, message_id) or {
                "id": message_id,
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "metadata": metadata,
            }
        else:
            message = chat_repository.create_message(
                ChatMessageCreate(
                    session_id=session_id,
                    role="assistant",
                    content=content,
                    metadata=metadata,
                )
            )
        if emit:
            await self._emit_session_message(
                session_id,
                message,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
        return message

    async def mirror_handoff_event_to_team_sessions(
        self,
        team_id: str,
        event: TeamHandoffEvent,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> None:
        for session in session_repository.get_sessions_by_runtime_team(team_id):
            await self._mirror_event_to_session(
                session.id,
                event,
                emit=True,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )

    def _build_delegated_user_message(
        self,
        source_role: str,
        target_role: str,
        leader_role: str,
        reason: str,
        work_summary: str,
        task_payload: str,
    ) -> str:
        parts = [
            f"Delegated task for role {target_role}.",
            f"Assigned by role: {source_role}",
            f"Leader role: {leader_role}",
            (
                "Your answer is a work-result report for the assigning or upstream agent. "
                "It is not a direct announcement that the user's overall task is complete."
            ),
            (
                "Only the leader may decide whether the user's overall task is complete. "
                "If more work is needed, hand off to the most suitable teammate."
            ),
            (
                "If you are not the leader, do not declare the user's task complete. "
                "Return a work report or hand off back to the leader for the final decision."
            ),
        ]
        if reason:
            parts.append(f"Delegation reason: {reason}")
        if work_summary:
            parts.append(f"Completed upstream work before handoff:\n{work_summary}")
        if task_payload:
            parts.append(f"Original user request:\n{task_payload}")
        parts.append(
            "When you finish this segment, your answer must include:\n"
            "- Completed work\n"
            "- Key results or modifications\n"
            "- Remaining issues, risks, or blockers\n"
            "- Recommended next step"
        )
        return "\n\n".join(parts)

    async def _run_role_session_turn(
        self,
        session_id: str,
        source_role: str,
        target_role: str,
        leader_role: str,
        reason: str,
        work_summary: str,
        task_payload: str,
        handoff_id: str,
        parent_handoff_id: Optional[str] = None,
        existing_user_message_id: Optional[int] = None,
        processed_message: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        session = session_repository.get_session(session_id, include_count=False)
        if not session:
            raise HTTPException(status_code=404, detail="Delegated session not found")

        config = config_repository.get_config(session.config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        baseline_tree_hash = self._capture_tree_hash(getattr(session, "work_path", None))

        if processed_message is None:
            user_msg_id, processed_message = await self._create_delegated_user_message(
                session_id=session.id,
                source_role=source_role,
                target_role=target_role,
                leader_role=leader_role,
                reason=reason,
                work_summary=work_summary,
                task_payload=task_payload,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
        else:
            user_msg_id = int(existing_user_message_id or 0)
            if user_msg_id <= 0:
                user_msg_id, processed_message = await self._create_delegated_user_message(
                    session_id=session.id,
                    source_role=source_role,
                    target_role=target_role,
                    leader_role=leader_role,
                    reason=reason,
                    work_summary=work_summary,
                    task_payload=task_payload,
                    handoff_id=handoff_id,
                    parent_handoff_id=parent_handoff_id,
                    inline_session_message_callback=inline_session_message_callback,
                    inline_session_ids=inline_session_ids,
                )
        assistant_msg = chat_repository.create_message(
            ChatMessageCreate(session_id=session.id, role="assistant", content="")
        )
        assistant_msg_id = int(assistant_msg.id)
        live_steps: list[Dict[str, Any]] = []
        await self._emit_session_message(
            session.id,
            self._build_live_assistant_snapshot(
                assistant_msg,
                live_steps,
                current_profile=target_role,
                content="",
                streaming=True,
            ),
            active_agent_profile=target_role,
        )
        sequence = 0
        final_answer: Optional[str] = None
        had_error = False
        handoff_count = 0
        max_handoffs = 16
        transferred_result: Optional[Dict[str, Any]] = None
        current_artifact_summary = ""
        current_changed_files: List[Dict[str, str]] = []
        current_artifact_source: Optional[str] = None

        context_summary = getattr(session, "context_summary", None) or ""
        last_compressed_call_id = getattr(session, "last_compressed_llm_call_id", None)
        last_compressed_message_id = (
            chat_repository.get_max_message_id_for_llm_call(session.id, int(last_compressed_call_id or 0))
            if last_compressed_call_id
            else None
        )

        llm_app_config = self.app_config.get("llm", {}) if isinstance(self.app_config, dict) else {}
        global_reasoning_summary = llm_app_config.get("reasoning_summary")
        if global_reasoning_summary:
            try:
                config.reasoning_summary = str(global_reasoning_summary)
            except Exception:
                pass

        agent_config = self.app_config.get("agent", {}) if isinstance(self.app_config, dict) else {}
        context_config = self.app_config.get("context", {}) if isinstance(self.app_config, dict) else {}
        ast_enabled = bool(agent_config.get("ast_enabled", True))
        code_map_cfg = agent_config.get("code_map", {}) if isinstance(agent_config, dict) else {}
        code_map_enabled = bool(code_map_cfg.get("enabled", True))
        react_max_iterations = agent_config.get("react_max_iterations", 50)
        try:
            react_max_iterations = int(react_max_iterations)
        except (TypeError, ValueError):
            react_max_iterations = 50

        current_profile = _normalize_text(getattr(session, "role_key", None) or getattr(session, "agent_profile", None))
        if not current_profile:
            raise HTTPException(status_code=400, detail="Delegated session has no active role")
        current_team_id = _normalize_text(getattr(session, "agent_team_id", None)) or None

        llm_client = create_llm_client(config)

        while True:
            updated_summary, updated_call_id, updated_message_id, did_global_compress = await maybe_compress_context(
                session_id=session.id,
                config=config,
                app_config=self.app_config,
                llm_client=llm_client,
                current_summary=context_summary,
                last_compressed_call_id=last_compressed_call_id,
                current_user_message_id=user_msg_id,
                current_user_text=processed_message,
            )
            if did_global_compress:
                context_summary = updated_summary
                last_compressed_call_id = updated_call_id
                last_compressed_message_id = updated_message_id
                try:
                    chat_repository.update_session_context(session.id, context_summary, last_compressed_call_id)
                except Exception:
                    pass

            private_context = chat_repository.get_agent_private_context(session.id, current_profile) or {}
            private_summary = str(private_context.get("context_summary") or "")
            private_after_step_id = private_context.get("last_compressed_step_id")
            private_summary, private_after_step_id, _ = await maybe_compress_private_context(
                session_id=session.id,
                agent_profile=current_profile,
                config=config,
                app_config=self.app_config,
                llm_client=llm_client,
                current_summary=private_summary,
                last_compressed_step_id=private_after_step_id,
                current_user_text=processed_message,
            )

            tool_context = {
                "app_config": self.app_config,
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
                include_tools=True,
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
                or getattr(session, "role_key", None) != current_profile
            ):
                session = session_repository.update_session(
                    session.id,
                    ChatSessionUpdate(agent_profile=current_profile, role_key=current_profile),
                ) or session

            code_map_prompt = None
            if "code_map" in ability_ids and ast_enabled and code_map_enabled:
                code_map_prompt = build_code_map_prompt(
                    session.id,
                    getattr(session, "work_path", None),
                )

            prompt_truncation_cfg = _build_prompt_truncation_config(context_config)
            history_for_llm = build_history_for_llm(
                session.id,
                last_compressed_message_id,
                user_msg_id,
                context_summary,
                code_map_prompt,
                prompt_truncation_cfg,
                current_agent_profile=current_profile,
                private_summary=private_summary,
                private_after_step_id=private_after_step_id,
            )

            turn_react_max_iterations = None if current_team_id else react_max_iterations

            executor = create_agent_executor(
                agent_type="react",
                llm_client=llm_client,
                tools=tools,
                max_iterations=turn_react_max_iterations,
                system_prompt=system_prompt,
            )

            request_overrides: Dict[str, Any] = {
                "_debug": {"session_id": session.id, "message_id": assistant_msg_id, "agent_profile": current_profile},
                "app_config": self.app_config,
                "current_agent_profile": current_profile,
                "current_agent_team_id": current_team_id,
                "work_path": getattr(session, "work_path", None),
                "prompt_truncation": prompt_truncation_cfg,
                "_context_state": {
                    "summary": context_summary,
                    "last_call_id": last_compressed_call_id,
                    "last_message_id": last_compressed_message_id,
                    "current_user_message_id": user_msg_id,
                    "current_agent_profile": current_profile,
                    "private_summary": private_summary,
                    "private_after_step_id": private_after_step_id,
                },
            }
            if code_map_prompt:
                request_overrides["_code_map_prompt"] = code_map_prompt

            handoff_request = None
            step_iter = executor.run(
                user_input=processed_message,
                history=history_for_llm,
                session_id=session.id,
                request_overrides=request_overrides,
            )

            try:
                async for step in step_iter:
                    if step.step_type == "context_estimate":
                        try:
                            session_repository.update_context_estimate(session.id, step.metadata)
                        except Exception:
                            pass
                        continue

                    if step.step_type.endswith("_delta"):
                        continue

                    suppress_prompt = False
                    if step.step_type == "error":
                        suppress_prompt = bool(step.metadata.get("suppress_prompt")) if isinstance(step.metadata, dict) else False
                    if suppress_prompt:
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

                    live_steps.append(step.to_dict())
                    sequence += 1
                    if step.step_type == "answer":
                        final_answer = step.content
                        await self._emit_session_message(
                            session.id,
                            self._build_live_assistant_snapshot(
                                assistant_msg,
                                live_steps,
                                current_profile=current_profile,
                                content=step.content,
                                streaming=False,
                            ),
                            active_agent_profile=current_profile,
                        )
                        break
                    if step.step_type == "error":
                        final_answer = step.content
                        had_error = True
                        await self._emit_session_message(
                            session.id,
                            self._build_live_assistant_snapshot(
                                assistant_msg,
                                live_steps,
                                current_profile=current_profile,
                                content=step.content,
                                streaming=False,
                            ),
                            active_agent_profile=current_profile,
                        )
                        break

                    await self._emit_session_message(
                        session.id,
                        self._build_live_assistant_snapshot(
                            assistant_msg,
                            live_steps,
                            current_profile=current_profile,
                            content="",
                            streaming=True,
                        ),
                        active_agent_profile=current_profile,
                    )

                    handoff_request = _extract_handoff_request(step)
                    if handoff_request:
                        break
            except asyncio.CancelledError:
                final_answer = "Delegated session cancelled."
                had_error = True
            except Exception as exc:
                final_answer = f"Delegated session failed: {exc}"
                had_error = True
                error_step = AgentStep(
                    step_type="error",
                    content=final_answer,
                    metadata={"error": str(exc)},
                )
                chat_repository.save_agent_step(
                    message_id=assistant_msg_id,
                    step_type=error_step.step_type,
                    content=error_step.content,
                    sequence=sequence,
                    metadata=error_step.metadata,
                    agent_profile=current_profile,
                )
                live_steps.append(error_step.to_dict())
                await self._emit_session_message(
                    session.id,
                    self._build_live_assistant_snapshot(
                        assistant_msg,
                        live_steps,
                        current_profile=current_profile,
                        content=final_answer,
                        streaming=False,
                    ),
                    active_agent_profile=current_profile,
                )
                sequence += 1

            if handoff_request:
                handoff_count += 1
                if handoff_count > max_handoffs:
                    final_answer = "Too many agent handoffs in a single delegated turn."
                    had_error = True
                    break
                current_artifact_summary, current_changed_files, current_artifact_source = self._collect_turn_artifacts(
                    getattr(session, "work_path", None),
                    baseline_tree_hash,
                    assistant_msg_id,
                )
                final_answer = ""
                chat_repository.update_message_content_and_metadata(
                    session.id,
                    assistant_msg_id,
                    final_answer,
                    {"agent_profile": current_profile},
                )
                await self._emit_session_message(
                    session.id,
                    self._build_live_assistant_snapshot(
                        assistant_msg,
                        live_steps,
                        current_profile=current_profile,
                        content=final_answer,
                        streaming=False,
                    ),
                    active_agent_profile=current_profile,
                )
                transferred_result = await self.execute_delegated_turn(
                    **self._build_execute_delegated_turn_kwargs(
                        source_session_id=session.id,
                        from_agent=current_profile,
                        to_agent=handoff_request["target_agent"],
                        reason=handoff_request["reason"],
                        work_summary=handoff_request.get("work_summary") or "",
                        task_payload=task_payload,
                        parent_handoff_id=handoff_id,
                        artifact_summary=current_artifact_summary or None,
                        changed_files=current_changed_files or None,
                        artifact_source=current_artifact_source,
                        inline_session_message_callback=inline_session_message_callback,
                        inline_session_ids=inline_session_ids,
                    )
                )
                break

            if final_answer is not None:
                break

            final_answer = "Delegated session produced no terminal work report."
            had_error = True
            break

        final_text = final_answer or ""
        if not transferred_result and not current_changed_files and not current_artifact_summary:
            current_artifact_summary, current_changed_files, current_artifact_source = self._collect_turn_artifacts(
                getattr(session, "work_path", None),
                baseline_tree_hash,
                assistant_msg_id,
            )
        chat_repository.update_message_content_and_metadata(
            session.id,
            assistant_msg_id,
            final_text,
            {"agent_profile": current_profile},
        )
        if transferred_result is not None:
            transferred_payload = dict(transferred_result)
            transferred_payload["artifact_summary"] = current_artifact_summary or None
            transferred_payload["changed_files"] = current_changed_files or None
            transferred_payload["artifact_source"] = current_artifact_source
            return transferred_payload
        return {
            "status": "ok" if final_text and not had_error else "error",
            "result": final_text if not had_error else "",
            "error": final_text if had_error else "",
            "assistant_message_id": assistant_msg_id,
            "session_id": session.id,
            "agent_profile": current_profile,
            "artifact_summary": current_artifact_summary or None,
            "changed_files": current_changed_files or None,
            "artifact_source": current_artifact_source,
            "artifact_owner_session_id": session.id,
            "artifact_owner_role_key": current_profile,
        }

    def _build_role_session_turn_kwargs(
        self,
        session_id: str,
        source_role: str,
        target_role: str,
        leader_role: str,
        reason: str,
        work_summary: str,
        task_payload: str,
        handoff_id: str,
        parent_handoff_id: Optional[str] = None,
        existing_user_message_id: Optional[int] = None,
        processed_message: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "session_id": session_id,
            "source_role": source_role,
            "target_role": target_role,
            "leader_role": leader_role,
            "reason": reason,
            "work_summary": work_summary,
            "task_payload": task_payload,
            "handoff_id": handoff_id,
            "parent_handoff_id": parent_handoff_id,
        }
        try:
            signature = inspect.signature(self._run_role_session_turn)
        except (TypeError, ValueError):
            signature = None

        if signature is None:
            if existing_user_message_id is not None:
                kwargs["existing_user_message_id"] = existing_user_message_id
            if processed_message is not None:
                kwargs["processed_message"] = processed_message
            kwargs["inline_session_message_callback"] = inline_session_message_callback
            kwargs["inline_session_ids"] = inline_session_ids
            return kwargs

        parameters = signature.parameters
        accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
        if existing_user_message_id is not None and (accepts_var_kwargs or "existing_user_message_id" in parameters):
            kwargs["existing_user_message_id"] = existing_user_message_id
        if processed_message is not None and (accepts_var_kwargs or "processed_message" in parameters):
            kwargs["processed_message"] = processed_message
        if accepts_var_kwargs or "inline_session_message_callback" in parameters:
            kwargs["inline_session_message_callback"] = inline_session_message_callback
        if accepts_var_kwargs or "inline_session_ids" in parameters:
            kwargs["inline_session_ids"] = inline_session_ids
        return kwargs

    def _build_execute_delegated_turn_kwargs(
        self,
        source_session_id: str,
        from_agent: str,
        to_agent: str,
        reason: str,
        work_summary: str,
        task_payload: str,
        parent_handoff_id: Optional[str] = None,
        artifact_summary: Optional[str] = None,
        changed_files: Optional[List[Dict[str, str]]] = None,
        artifact_source: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "source_session_id": source_session_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "work_summary": work_summary,
            "task_payload": task_payload,
            "parent_handoff_id": parent_handoff_id,
        }
        try:
            signature = inspect.signature(self.execute_delegated_turn)
        except (TypeError, ValueError):
            signature = None

        if signature is None:
            if artifact_summary is not None:
                kwargs["artifact_summary"] = artifact_summary
            if changed_files is not None:
                kwargs["changed_files"] = changed_files
            if artifact_source is not None:
                kwargs["artifact_source"] = artifact_source
            kwargs["inline_session_message_callback"] = inline_session_message_callback
            kwargs["inline_session_ids"] = inline_session_ids
            return kwargs

        parameters = signature.parameters
        accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
        if artifact_summary is not None and (accepts_var_kwargs or "artifact_summary" in parameters):
            kwargs["artifact_summary"] = artifact_summary
        if changed_files is not None and (accepts_var_kwargs or "changed_files" in parameters):
            kwargs["changed_files"] = changed_files
        if artifact_source is not None and (accepts_var_kwargs or "artifact_source" in parameters):
            kwargs["artifact_source"] = artifact_source
        if accepts_var_kwargs or "inline_session_message_callback" in parameters:
            kwargs["inline_session_message_callback"] = inline_session_message_callback
        if accepts_var_kwargs or "inline_session_ids" in parameters:
            kwargs["inline_session_ids"] = inline_session_ids
        return kwargs

    async def execute_delegated_turn(
        self,
        source_session_id: str,
        from_agent: str,
        to_agent: str,
        reason: str,
        work_summary: str,
        task_payload: str,
        parent_handoff_id: Optional[str] = None,
        artifact_summary: Optional[str] = None,
        changed_files: Optional[List[Dict[str, str]]] = None,
        artifact_source: Optional[str] = None,
        inline_session_message_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        inline_session_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        normalized_artifact_summary = _normalize_text(artifact_summary)
        normalized_changed_files = _normalize_changed_files(changed_files)
        normalized_artifact_source = _normalize_artifact_source(artifact_source)
        if not normalized_artifact_summary:
            normalized_artifact_summary = _build_artifact_summary(normalized_changed_files)
        source_session = session_repository.get_session(source_session_id, include_count=False)
        self.team.ensure_handoff_allowed(
            from_agent,
            to_agent,
            active_team_id=getattr(source_session, "agent_team_id", None) if source_session else None,
        )
        runtime_team, source_session = self.ensure_team(source_session_id, from_agent)
        handoff_id = str(uuid.uuid4())
        leader_role = self.resolve_leader_role(getattr(source_session, "agent_team_id", None), fallback_role=from_agent)
        source_role_key = _normalize_text(
            getattr(source_session, "role_key", None) or getattr(source_session, "agent_profile", None)
        )

        target_session, target_created = await self.resolve_or_create_role_session(
            runtime_team.id,
            to_agent,
            source_session,
            backfill_history=False,
        )
        delegated_user_message_id: Optional[int] = None
        processed_task_payload: Optional[str] = None
        if not (leader_role and to_agent == leader_role and source_role_key and source_role_key != leader_role):
            delegated_user_message_id, processed_task_payload = await self._create_delegated_user_message(
                session_id=target_session.id,
                source_role=from_agent,
                target_role=to_agent,
                leader_role=leader_role or from_agent,
                reason=reason,
                work_summary=work_summary,
                task_payload=task_payload,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
        if target_created:
            await self._backfill_team_history_to_session(target_session.id, runtime_team.id)
        requested_event = self.append_handoff_event(
            team_id=runtime_team.id,
            handoff_id=handoff_id,
            parent_handoff_id=parent_handoff_id,
            event_kind="requested",
            from_session_id=source_session.id,
            from_role_key=from_agent,
            to_session_id=target_session.id,
            to_role_key=to_agent,
            reason=reason,
            work_summary=work_summary,
            task_payload=task_payload,
        )
        await self.mirror_handoff_event_to_team_sessions(
            runtime_team.id,
            requested_event,
            inline_session_message_callback=inline_session_message_callback,
            inline_session_ids=inline_session_ids,
        )

        started_event = self.append_handoff_event(
            team_id=runtime_team.id,
            handoff_id=handoff_id,
            parent_handoff_id=parent_handoff_id,
            event_kind="started",
            from_session_id=source_session.id,
            from_role_key=from_agent,
            to_session_id=target_session.id,
            to_role_key=to_agent,
            reason=reason,
            work_summary=work_summary,
            task_payload=task_payload,
        )
        await self.mirror_handoff_event_to_team_sessions(
            runtime_team.id,
            started_event,
            inline_session_message_callback=inline_session_message_callback,
            inline_session_ids=inline_session_ids,
        )

        if leader_role and to_agent == leader_role and source_role_key and source_role_key != leader_role:
            return_summary = self._build_return_to_leader_report(from_agent, leader_role, reason, work_summary)
            completed_event = self.append_handoff_event(
                team_id=runtime_team.id,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                event_kind="completed",
                from_session_id=source_session.id,
                from_role_key=from_agent,
                to_session_id=target_session.id,
                to_role_key=to_agent,
                reason=reason,
                work_summary=work_summary,
                artifact_summary=normalized_artifact_summary,
                changed_files=normalized_changed_files,
                artifact_source=normalized_artifact_source,
                artifact_owner_session_id=source_session.id,
                artifact_owner_role_key=from_agent,
                result_summary=_summarize_result(return_summary),
            )
            await self.mirror_handoff_event_to_team_sessions(
                runtime_team.id,
                completed_event,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
            return {
                "status": "returned_to_leader",
                "handoff_id": handoff_id,
                "source_session_id": source_session.id,
                "target_session_id": target_session.id,
                "from_role_key": from_agent,
                "to_role_key": to_agent,
                "reason": reason,
                "work_summary": work_summary,
                "return_summary": return_summary,
                "artifact_summary": normalized_artifact_summary or None,
                "changed_files": normalized_changed_files or None,
                "artifact_source": normalized_artifact_source,
                "artifact_owner_session_id": source_session.id,
                "artifact_owner_role_key": from_agent,
            }

        try:
            result = await self._run_role_session_turn(
                **self._build_role_session_turn_kwargs(
                    session_id=target_session.id,
                    source_role=from_agent,
                    target_role=to_agent,
                    leader_role=leader_role or from_agent,
                    reason=reason,
                    work_summary=work_summary,
                    task_payload=task_payload,
                    handoff_id=handoff_id,
                    parent_handoff_id=parent_handoff_id,
                    existing_user_message_id=delegated_user_message_id,
                    processed_message=processed_task_payload,
                    inline_session_message_callback=inline_session_message_callback,
                    inline_session_ids=inline_session_ids,
                )
            )
            result_artifact_summary = _normalize_text(result.get("artifact_summary"))
            result_changed_files = _normalize_changed_files(result.get("changed_files"))
            result_artifact_source = _normalize_artifact_source(result.get("artifact_source"))
            result_artifact_owner_session_id = _normalize_text(result.get("artifact_owner_session_id")) or None
            result_artifact_owner_role_key = _normalize_text(result.get("artifact_owner_role_key")) or None
            if not result_artifact_summary:
                result_artifact_summary = _build_artifact_summary(result_changed_files)
            if result.get("status") == "returned_to_leader":
                returning_role = _normalize_text(result.get("from_role_key")) or to_agent
                leader_target = _normalize_text(result.get("to_role_key")) or (leader_role or from_agent)
                return_summary = str(
                    result.get("return_summary")
                    or self._build_return_to_leader_report(
                        returning_role,
                        leader_target,
                        result.get("reason"),
                        result.get("work_summary"),
                    )
                ).strip()
                completed_event = self.append_handoff_event(
                    team_id=runtime_team.id,
                    handoff_id=handoff_id,
                    parent_handoff_id=parent_handoff_id,
                    event_kind="completed",
                    from_session_id=source_session.id,
                    from_role_key=from_agent,
                    to_session_id=target_session.id,
                    to_role_key=to_agent,
                    reason=reason,
                    work_summary=work_summary,
                    artifact_summary=result_artifact_summary,
                    changed_files=result_changed_files,
                    artifact_source=result_artifact_source,
                    artifact_owner_session_id=result_artifact_owner_session_id,
                    artifact_owner_role_key=result_artifact_owner_role_key,
                    result_summary=_summarize_result(return_summary),
                )
                await self.mirror_handoff_event_to_team_sessions(
                    runtime_team.id,
                    completed_event,
                    inline_session_message_callback=inline_session_message_callback,
                    inline_session_ids=inline_session_ids,
                )
                if leader_role and source_role_key == leader_role:
                    await self.append_delegated_result_message(
                        session_id=source_session.id,
                        team_id=runtime_team.id,
                        handoff_id=handoff_id,
                        from_role_key=leader_role,
                        to_role_key=returning_role,
                        source_session_id=source_session.id,
                        target_session_id=target_session.id,
                        content=return_summary,
                        status="ok",
                        artifact_summary=result_artifact_summary,
                        changed_files=result_changed_files,
                        artifact_source=result_artifact_source,
                        artifact_owner_session_id=result_artifact_owner_session_id,
                        artifact_owner_role_key=result_artifact_owner_role_key,
                        inline_session_message_callback=inline_session_message_callback,
                        inline_session_ids=inline_session_ids,
                    )
                    return {
                        "status": "ok",
                        "result": return_summary,
                        "handoff_id": handoff_id,
                        "source_session_id": source_session.id,
                        "target_session_id": target_session.id,
                        "from_role_key": leader_role,
                        "to_role_key": returning_role,
                        "artifact_summary": result_artifact_summary or None,
                        "changed_files": result_changed_files or None,
                        "artifact_source": result_artifact_source,
                        "artifact_owner_session_id": result_artifact_owner_session_id,
                        "artifact_owner_role_key": result_artifact_owner_role_key,
                    }
                return {
                    "status": "returned_to_leader",
                    "handoff_id": handoff_id,
                    "source_session_id": source_session.id,
                    "target_session_id": target_session.id,
                    "from_role_key": returning_role,
                    "to_role_key": leader_target,
                    "reason": result.get("reason"),
                    "work_summary": result.get("work_summary"),
                    "return_summary": return_summary,
                    "artifact_summary": result_artifact_summary or None,
                    "changed_files": result_changed_files or None,
                    "artifact_source": result_artifact_source,
                    "artifact_owner_session_id": result_artifact_owner_session_id,
                    "artifact_owner_role_key": result_artifact_owner_role_key,
                }
            final_text = str(result.get("result") or result.get("error") or "").strip()
            if final_text and result.get("status") == "ok":
                completed_event = self.append_handoff_event(
                    team_id=runtime_team.id,
                    handoff_id=handoff_id,
                    parent_handoff_id=parent_handoff_id,
                    event_kind="completed",
                    from_session_id=source_session.id,
                    from_role_key=from_agent,
                    to_session_id=target_session.id,
                    to_role_key=to_agent,
                    reason=reason,
                    work_summary=work_summary,
                    artifact_summary=result_artifact_summary,
                    changed_files=result_changed_files,
                    artifact_source=result_artifact_source,
                    artifact_owner_session_id=result_artifact_owner_session_id,
                    artifact_owner_role_key=result_artifact_owner_role_key,
                    result_summary=_summarize_result(final_text),
                )
                await self.mirror_handoff_event_to_team_sessions(
                    runtime_team.id,
                    completed_event,
                    inline_session_message_callback=inline_session_message_callback,
                    inline_session_ids=inline_session_ids,
                )
                await self.append_delegated_result_message(
                    session_id=source_session.id,
                    team_id=runtime_team.id,
                    handoff_id=handoff_id,
                    from_role_key=from_agent,
                    to_role_key=to_agent,
                    source_session_id=source_session.id,
                    target_session_id=target_session.id,
                    content=final_text,
                    status="ok",
                    artifact_summary=result_artifact_summary,
                    changed_files=result_changed_files,
                    artifact_source=result_artifact_source,
                    artifact_owner_session_id=result_artifact_owner_session_id,
                    artifact_owner_role_key=result_artifact_owner_role_key,
                    inline_session_message_callback=inline_session_message_callback,
                    inline_session_ids=inline_session_ids,
                )
                return {
                    "status": "ok",
                    "result": final_text,
                    "handoff_id": handoff_id,
                    "source_session_id": source_session.id,
                    "target_session_id": target_session.id,
                    "from_role_key": from_agent,
                    "to_role_key": to_agent,
                    "artifact_summary": result_artifact_summary or None,
                    "changed_files": result_changed_files or None,
                    "artifact_source": result_artifact_source,
                    "artifact_owner_session_id": result_artifact_owner_session_id,
                    "artifact_owner_role_key": result_artifact_owner_role_key,
                }

            error_text = final_text or "Delegated session produced no final answer"
            failed_event = self.append_handoff_event(
                team_id=runtime_team.id,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                event_kind="failed",
                from_session_id=source_session.id,
                from_role_key=from_agent,
                to_session_id=target_session.id,
                to_role_key=to_agent,
                reason=reason,
                work_summary=work_summary,
                artifact_summary=result_artifact_summary,
                changed_files=result_changed_files,
                artifact_source=result_artifact_source,
                artifact_owner_session_id=result_artifact_owner_session_id,
                artifact_owner_role_key=result_artifact_owner_role_key,
                error=error_text,
            )
            await self.mirror_handoff_event_to_team_sessions(
                runtime_team.id,
                failed_event,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
            await self.append_delegated_result_message(
                session_id=source_session.id,
                team_id=runtime_team.id,
                handoff_id=handoff_id,
                from_role_key=from_agent,
                to_role_key=to_agent,
                source_session_id=source_session.id,
                target_session_id=target_session.id,
                content=error_text,
                status="error",
                artifact_summary=result_artifact_summary,
                changed_files=result_changed_files,
                artifact_source=result_artifact_source,
                artifact_owner_session_id=result_artifact_owner_session_id,
                artifact_owner_role_key=result_artifact_owner_role_key,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
            return {
                "status": "error",
                "error": error_text,
                "handoff_id": handoff_id,
                "source_session_id": source_session.id,
                "target_session_id": target_session.id,
                "from_role_key": from_agent,
                "to_role_key": to_agent,
                "artifact_summary": result_artifact_summary or None,
                "changed_files": result_changed_files or None,
                "artifact_source": result_artifact_source,
                "artifact_owner_session_id": result_artifact_owner_session_id,
                "artifact_owner_role_key": result_artifact_owner_role_key,
            }
        except Exception as exc:
            failed_event = self.append_handoff_event(
                team_id=runtime_team.id,
                handoff_id=handoff_id,
                parent_handoff_id=parent_handoff_id,
                event_kind="failed",
                from_session_id=source_session.id,
                from_role_key=from_agent,
                to_session_id=target_session.id,
                to_role_key=to_agent,
                reason=reason,
                work_summary=work_summary,
                error=str(exc),
            )
            await self.mirror_handoff_event_to_team_sessions(
                runtime_team.id,
                failed_event,
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
            await self.append_delegated_result_message(
                session_id=source_session.id,
                team_id=runtime_team.id,
                handoff_id=handoff_id,
                from_role_key=from_agent,
                to_role_key=to_agent,
                source_session_id=source_session.id,
                target_session_id=target_session.id,
                content=str(exc),
                status="error",
                inline_session_message_callback=inline_session_message_callback,
                inline_session_ids=inline_session_ids,
            )
            return {
                "status": "error",
                "error": str(exc),
                "handoff_id": handoff_id,
                "source_session_id": source_session.id,
                "target_session_id": target_session.id,
                "from_role_key": from_agent,
                "to_role_key": to_agent,
            }
