from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from agents.execution.directives import ExecutionDirective, TERMINAL_DIRECTIVE_KINDS
from agents.execution.handoff_support import resolve_handoff_target

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage
    from repositories.agent_profile_repository import AgentProfileRepository
    from repositories.shared_fact_repository import SharedFactRepository
    from run_manager import RunManager


class DirectiveRunner:
    def __init__(
        self,
        agent: "AgentBase",
        *,
        run_manager: Optional["RunManager"] = None,
        profile_repository: Optional["AgentProfileRepository"] = None,
        shared_fact_repository: Optional["SharedFactRepository"] = None,
    ) -> None:
        self.agent = agent
        self.run_manager = run_manager
        self.profile_repository = profile_repository
        self.shared_fact_repository = shared_fact_repository

    async def execute(
        self,
        source_message: "AgentMessage",
        directive: ExecutionDirective,
    ) -> Any:
        handlers = {
            "handoff": self._handoff,
            "send_rpc_request": self._send_rpc_request,
            "send_rpc_response": self._send_rpc_response,
            "send_event": self._send_event,
            "broadcast_event": self._broadcast_event,
            "finish_run": self._finish_run,
            "fail_run": self._fail_run,
            "stop_run": self._stop_run,
        }
        handler = handlers.get(directive.kind)
        if handler is None:
            raise RuntimeError(f"Unsupported directive: {directive.kind}")
        return await handler(source_message, directive.args)

    async def _send_rpc_request(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> Any:
        return await self.agent.send_rpc_request(
            str(args.get("topic") or "").strip(),
            self._as_dict(args.get("payload")),
            target_agent_id=self._optional_text(args.get("target_agent_id")),
            target_profile=self._optional_text(args.get("target_profile")),
            run_id=source_message.run_id,
            session_id=source_message.session_id,
            timeout_ms=self._optional_int(args.get("timeout_ms")) or 300_000,
            visibility=self._optional_text(args.get("visibility")) or "public",
            level=self._optional_text(args.get("level")) or "info",
        )

    async def _send_rpc_response(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> Any:
        session_id = self._optional_text(source_message.session_id)
        if not session_id:
            raise RuntimeError("send_rpc_response requires session_id")
        reply_to_message_id = self._optional_text(args.get("reply_to_message_id"))
        request = None
        if (
            reply_to_message_id is None
            and source_message.message_type == "rpc"
            and source_message.rpc_phase == "request"
        ):
            request = source_message
        elif source_message.id == reply_to_message_id:
            request = source_message
        elif self.shared_fact_repository is not None and reply_to_message_id is not None:
            fact = await self.shared_fact_repository.get_by_message_id(
                session_id,
                reply_to_message_id,
            )
            if fact is not None:
                request = self.shared_fact_repository.to_agent_message(fact)
        if request is None:
            raise RuntimeError(
                f"reply target message not found: {reply_to_message_id or source_message.id}"
            )
        if request.message_type != "rpc" or request.rpc_phase != "request":
            raise RuntimeError("send_rpc_response can only reply to rpc request messages")
        return await self.agent.reply_rpc(
            request,
            self._as_dict(args.get("payload")),
            ok=bool(args.get("ok", True)),
            visibility=self._optional_text(args.get("visibility")) or None,
            level=self._optional_text(args.get("level")) or "info",
        )

    async def _send_event(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> Any:
        return await self.agent.send_event(
            str(args.get("topic") or "").strip(),
            self._as_dict(args.get("payload")),
            target_agent_id=self._optional_text(args.get("target_agent_id")),
            target_profile=self._optional_text(args.get("target_profile")),
            run_id=source_message.run_id,
            session_id=source_message.session_id,
            visibility=self._optional_text(args.get("visibility")) or "public",
            level=self._optional_text(args.get("level")) or "info",
        )

    async def _handoff(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> Any:
        resolved = await resolve_handoff_target(
            self.profile_repository,
            target_profile=str(args.get("target_profile") or ""),
            current_profile_id=self._current_profile_id(),
        )
        instruction = self._required_text(args.get("instruction"), "handoff requires instruction")
        reason = self._optional_text(args.get("reason"))
        context = self._as_dict(args.get("context"))

        payload: Dict[str, Any] = {
            "content": instruction,
            "handoff_from_agent_id": self.agent.agent_id,
            "handoff_to_profile": resolved.profile.id,
        }
        current_profile_id = self._current_profile_id()
        if current_profile_id is not None:
            payload["handoff_from_profile"] = current_profile_id
        if reason is not None:
            payload["handoff_reason"] = reason
        if context:
            payload["handoff_context"] = context
            request_overrides = context.get("request_overrides")
            if isinstance(request_overrides, dict):
                payload["request_overrides"] = await self._normalize_handoff_request_overrides(
                    source_message,
                    request_overrides,
                    fallback_instruction=instruction,
                )

        return await self.agent.send_event(
            resolved.topic,
            payload,
            target_profile=resolved.profile.id,
            run_id=source_message.run_id,
            session_id=source_message.session_id,
            visibility=self._optional_text(args.get("visibility")) or "public",
            level=self._optional_text(args.get("level")) or "info",
        )

    async def _broadcast_event(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> Any:
        return await self.agent.broadcast_event(
            str(args.get("topic") or "").strip(),
            self._as_dict(args.get("payload")),
            run_id=source_message.run_id,
            session_id=source_message.session_id,
            visibility=self._optional_text(args.get("visibility")) or "public",
            level=self._optional_text(args.get("level")) or "info",
        )

    async def _finish_run(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> None:
        run_manager = self._require_run_manager("finish_run")
        run_id = self._optional_text(source_message.run_id)
        if not run_id:
            raise RuntimeError("finish_run requires run_id")
        payload = self._as_dict(args.get("payload"))
        payload.setdefault("reply", str(args.get("reply") or ""))
        payload.setdefault("status", self._optional_text(args.get("status")) or "completed")
        await run_manager.finish_run(run_id, payload, message_id=source_message.id)

    async def _fail_run(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> None:
        run_manager = self._require_run_manager("fail_run")
        run_id = self._optional_text(source_message.run_id)
        if not run_id:
            raise RuntimeError("fail_run requires run_id")
        error_text = str(args.get("error") or "").strip() or "run failed"
        await run_manager.fail_run(
            run_id,
            error_text,
            message_id=source_message.id,
            result_payload=self._as_dict(args.get("payload")),
        )

    async def _stop_run(
        self,
        source_message: "AgentMessage",
        args: Dict[str, Any],
    ) -> None:
        run_manager = self._require_run_manager("stop_run")
        run_id = self._optional_text(source_message.run_id)
        if not run_id:
            raise RuntimeError("stop_run requires run_id")
        await run_manager.stop_run(run_id)
        reason = self._optional_text(args.get("reason")) or "stop_run"
        await self.agent.update_status(
            "idle",
            reason=reason,
            metadata={"run_id": run_id},
        )

    def _require_run_manager(self, directive: str) -> "RunManager":
        if self.run_manager is None:
            raise RuntimeError(f"{directive} requires RunManager")
        return self.run_manager

    def _as_dict(self, value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _optional_text(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    def _required_text(self, value: Any, error_text: str) -> str:
        text = self._optional_text(value)
        if text is None:
            raise RuntimeError(error_text)
        return text

    async def _normalize_handoff_request_overrides(
        self,
        source_message: "AgentMessage",
        request_overrides: Dict[str, Any],
        *,
        fallback_instruction: str,
    ) -> Dict[str, Any]:
        normalized = dict(request_overrides)
        tool_name = self._optional_text(normalized.get("tool_name"))
        if tool_name not in TERMINAL_DIRECTIVE_KINDS:
            return normalized

        controller_agent_id = await self._resolve_controller_agent_id(source_message.run_id)
        if controller_agent_id is None:
            raise RuntimeError("handoff terminal relay requires active controller agent")

        relay_payload: Dict[str, Any] = {
            "content": self._relay_content_for_terminal_override(
                tool_name,
                normalized,
                fallback_instruction=fallback_instruction,
            ),
            "request_overrides": normalized,
        }
        return {
            "tool_name": "send_event",
            "tool_arguments": {
                "topic": "run.controller.input",
                "target_agent_id": controller_agent_id,
                "payload": relay_payload,
            },
        }

    async def _resolve_controller_agent_id(self, run_id: Optional[str]) -> Optional[str]:
        normalized_run_id = self._optional_text(run_id)
        if normalized_run_id is None or self.run_manager is None:
            return None
        active_run = await self.run_manager.get_active_run(normalized_run_id)
        if active_run is None:
            return None
        return self._optional_text(active_run.controller_agent_id)

    def _relay_content_for_terminal_override(
        self,
        tool_name: str,
        request_overrides: Dict[str, Any],
        *,
        fallback_instruction: str,
    ) -> str:
        arguments = request_overrides.get("tool_arguments")
        args = dict(arguments) if isinstance(arguments, dict) else {}
        if tool_name == "finish_run":
            return self._optional_text(args.get("reply")) or fallback_instruction
        if tool_name == "fail_run":
            return self._optional_text(args.get("error")) or fallback_instruction
        if tool_name == "stop_run":
            return self._optional_text(args.get("reason")) or fallback_instruction
        return fallback_instruction

    def _optional_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _current_profile_id(self) -> Optional[str]:
        value = getattr(getattr(self.agent, "instance", None), "profile_id", None)
        return self._optional_text(value)
