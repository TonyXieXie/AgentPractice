from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from agents.execution.directives import ExecutionDirective

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage
    from repositories.message_center_repository import MessageCenterRepository
    from run_manager import RunManager


class DirectiveRunner:
    def __init__(
        self,
        agent: "AgentBase",
        *,
        run_manager: Optional["RunManager"] = None,
        message_center_repository: Optional["MessageCenterRepository"] = None,
    ) -> None:
        self.agent = agent
        self.run_manager = run_manager
        self.message_center_repository = message_center_repository

    async def execute(
        self,
        source_message: "AgentMessage",
        directive: ExecutionDirective,
    ) -> Any:
        handlers = {
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
        if not reply_to_message_id:
            raise RuntimeError("reply_to_message_id is required")
        request = None
        if source_message.id == reply_to_message_id:
            request = source_message
        elif self.message_center_repository is not None:
            request = await self.message_center_repository.get_shared_message(
                session_id,
                reply_to_message_id,
            )
        if request is None:
            raise RuntimeError(f"reply target message not found: {reply_to_message_id}")
        if request.message_type != "rpc" or request.rpc_phase != "request":
            raise RuntimeError("send_rpc_response can only reply to rpc request messages")
        return await self.agent.reply_rpc(
            request,
            self._as_dict(args.get("payload")),
            ok=bool(args.get("ok")),
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

    def _optional_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
