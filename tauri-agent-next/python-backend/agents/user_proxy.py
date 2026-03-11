from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agents.base import AgentBase
from agents.execution.message_utils import get_session_id
from agents.message import AgentMessage
from models import CreateRunAcceptedResponse, CreateRunRequest


@dataclass(slots=True)
class PendingTopLevelRequest:
    correlation_id: str
    run_id: str
    session_id: str
    assistant_agent_id: str


class UserProxyAgent(AgentBase):
    def __init__(
        self,
        *args,
        run_manager: "RunManager",
        roster_manager: "AgentRosterManager",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.run_manager = run_manager
        self.roster_manager = roster_manager
        self.received_events: list[AgentMessage] = []
        self._pending_top_level_requests: dict[str, PendingTopLevelRequest] = {}
        self._pending_lock = asyncio.Lock()

    async def send_user_message(
        self,
        content: str,
        *,
        target_agent_id: str,
        topic: str = "task.run",
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        strategy: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        payload_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        message = self._build_user_message_request(
            content,
            target_agent_id=target_agent_id,
            topic=topic,
            run_id=run_id,
            session_id=session_id,
            strategy=strategy,
            history=history,
            llm_config=llm_config,
            system_prompt=system_prompt,
            work_path=work_path,
            request_overrides=request_overrides,
            payload_overrides=payload_overrides,
            metadata=metadata,
        )
        await self.center.route(message)
        return message

    def _build_user_message_request(
        self,
        content: str,
        *,
        target_agent_id: str,
        topic: str = "task.run",
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        strategy: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        payload_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        payload: Dict[str, Any] = {"content": content}
        if strategy:
            payload["strategy"] = strategy
        if session_id is not None:
            payload["session_id"] = session_id
        if history is not None:
            payload["history"] = deepcopy(history)
        if llm_config is not None:
            payload["llm_config"] = deepcopy(llm_config)
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if work_path is not None:
            payload["work_path"] = work_path
        if request_overrides is not None:
            payload["request_overrides"] = deepcopy(request_overrides)
        if payload_overrides:
            payload.update(deepcopy(payload_overrides))
        return self._build_outbound_rpc_request(
            topic=topic,
            payload=payload,
            target_agent_id=target_agent_id,
            run_id=run_id or self.run_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def on_message(self, message: AgentMessage):
        if message.message_type == "event":
            self.received_events.append(message)
            return None

        if message.message_type == "rpc" and message.rpc_phase == "response":
            await self._handle_top_level_response(message)
            return None

        if message.message_type != "rpc" or message.rpc_phase != "request":
            return None

        if message.topic == "run.submit":
            await self._handle_run_submit(message)
            return None
        if message.topic == "run.stop":
            await self._handle_run_stop(message)
            return None

        await self.reply_rpc(
            message,
            {"error": f"Unsupported topic: {message.topic}"},
            ok=False,
            level="error",
            visibility="internal",
        )
        return None

    async def _handle_run_submit(self, message: AgentMessage) -> None:
        from run_manager import SessionBusyError

        request = CreateRunRequest.model_validate(dict(message.payload or {}))
        session_id = get_session_id(message) or request.session_id
        if not session_id:
            await self.reply_rpc(
                message,
                {"error": "session_id is required", "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        request = request.model_copy(update={"session_id": session_id})

        active_run = await self.run_manager.get_active_run_by_session(session_id)
        if active_run is not None:
            await self.reply_rpc(
                message,
                {
                    "error": f"session busy: {session_id}",
                    "status": "busy",
                    "session_id": session_id,
                    "active_run_id": active_run.run_id,
                },
                ok=False,
                level="error",
                visibility="internal",
            )
            return

        try:
            response = await self._start_run(request)
        except SessionBusyError as exc:
            await self.reply_rpc(
                message,
                {
                    "error": str(exc),
                    "status": "busy",
                    "session_id": exc.session_id,
                    "active_run_id": exc.active_run_id,
                },
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        except Exception as exc:
            await self.reply_rpc(
                message,
                {"error": str(exc), "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return

        await self.reply_rpc(message, response.model_dump(), ok=True)

    async def _handle_run_stop(self, message: AgentMessage) -> None:
        payload = dict(message.payload or {})
        run_id = str(payload.get("run_id") or message.run_id or "").strip()
        if not run_id:
            await self.reply_rpc(
                message,
                {"error": "run_id is required", "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        await self._clear_pending_top_level_request_by_run(run_id)
        response = await self.run_manager.stop_run(run_id)
        if response is None:
            await self.reply_rpc(
                message,
                {"error": f"run not found: {run_id}", "status": "not_found", "run_id": run_id},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        await self.update_status(
            "idle",
            reason="run.stop",
            metadata={"run_id": run_id, "stop_status": response.status},
        )
        await self.reply_rpc(message, response.model_dump(), ok=True)

    async def _start_run(self, request: CreateRunRequest) -> CreateRunAcceptedResponse:
        session_id = str(request.session_id or "").strip()
        if not session_id:
            raise RuntimeError("session_id is required")

        assistant_record = await self.roster_manager.ensure_primary_entry_assistant(session_id)
        strategy = self._resolve_requested_strategy(request)
        active_run = await self.run_manager.open_run(
            session_id,
            assistant_record.id,
            metadata={
                "user_agent_id": self.agent_id,
                "assistant_agent_id": assistant_record.id,
                "strategy": strategy,
            },
        )
        top_level_request = self._build_user_message_request(
            request.content,
            target_agent_id=assistant_record.id,
            run_id=active_run.run_id,
            session_id=session_id,
            strategy=request.strategy,
            history=request.history,
            llm_config=request.llm_config,
            system_prompt=request.system_prompt,
            work_path=request.work_path,
            request_overrides=request.request_overrides,
        )
        correlation_id = str(top_level_request.correlation_id or top_level_request.id)
        try:
            await self._put_pending_top_level_request(
                PendingTopLevelRequest(
                    correlation_id=correlation_id,
                    run_id=active_run.run_id,
                    session_id=session_id,
                    assistant_agent_id=assistant_record.id,
                )
            )
            await self.update_status(
                "waiting",
                reason="task.run",
                metadata={
                    "run_id": active_run.run_id,
                    "session_id": session_id,
                    "assistant_agent_id": assistant_record.id,
                    "correlation_id": correlation_id,
                },
            )
            await self.center.route(top_level_request)
        except Exception as exc:
            await self._clear_pending_top_level_request(correlation_id)
            await self.run_manager.fail_run(active_run.run_id, str(exc))
            await self.update_status(
                "idle",
                reason="run.dispatch_failed",
                metadata={"run_id": active_run.run_id},
            )
            raise

        return CreateRunAcceptedResponse(
            run_id=active_run.run_id,
            session_id=session_id,
            user_agent_id=self.agent_id,
            assistant_agent_id=assistant_record.id,
        )

    async def _handle_top_level_response(self, message: AgentMessage) -> None:
        correlation_id = str(message.correlation_id or "").strip()
        if not correlation_id:
            await self._observe_ignored_response(message, reason="missing_correlation")
            return

        pending = await self._pop_pending_top_level_request(correlation_id)
        if pending is None:
            await self._observe_ignored_response(message, reason="unknown_correlation")
            return

        active_run = await self.run_manager.get_active_run(pending.run_id)
        if active_run is None:
            await self._observe_ignored_response(
                message,
                reason="inactive_run",
                pending=pending,
            )
            return

        if message.ok:
            await self.run_manager.finish_run(
                pending.run_id,
                dict(message.payload or {}),
                message_id=message.correlation_id or message.id,
            )
        else:
            error_text = str(
                message.payload.get("error")
                or message.payload.get("reply")
                or "run failed"
            )
            await self.run_manager.fail_run(
                pending.run_id,
                error_text,
                message_id=message.correlation_id or message.id,
                result_payload=dict(message.payload or {}),
            )
        await self.update_status(
            "idle",
            reason="task.run.completed",
            metadata={
                "run_id": pending.run_id,
                "session_id": pending.session_id,
                "assistant_agent_id": pending.assistant_agent_id,
                "correlation_id": pending.correlation_id,
                "result_ok": bool(message.ok),
            },
        )

    async def _put_pending_top_level_request(self, pending: PendingTopLevelRequest) -> None:
        async with self._pending_lock:
            self._pending_top_level_requests[pending.correlation_id] = pending

    async def _pop_pending_top_level_request(
        self,
        correlation_id: str,
    ) -> Optional[PendingTopLevelRequest]:
        normalized = str(correlation_id or "").strip()
        if not normalized:
            return None
        async with self._pending_lock:
            return self._pending_top_level_requests.pop(normalized, None)

    async def _clear_pending_top_level_request(
        self,
        correlation_id: str,
    ) -> Optional[PendingTopLevelRequest]:
        return await self._pop_pending_top_level_request(correlation_id)

    async def _clear_pending_top_level_request_by_run(
        self,
        run_id: str,
    ) -> Optional[PendingTopLevelRequest]:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        async with self._pending_lock:
            for correlation_id, pending in list(self._pending_top_level_requests.items()):
                if pending.run_id != normalized:
                    continue
                self._pending_top_level_requests.pop(correlation_id, None)
                return pending
        return None

    async def _observe_ignored_response(
        self,
        message: AgentMessage,
        *,
        reason: str,
        pending: Optional[PendingTopLevelRequest] = None,
    ) -> None:
        payload = {
            "topic": message.topic,
            "sender_id": message.sender_id,
            "correlation_id": message.correlation_id,
            "reason": reason,
        }
        if pending is not None:
            payload.update(
                {
                    "run_id": pending.run_id,
                    "session_id": pending.session_id,
                    "assistant_agent_id": pending.assistant_agent_id,
                }
            )
        await self.observe(
            "user_proxy.rpc_response_ignored",
            payload=payload,
            run_id=message.run_id,
            message_id=message.id,
            visibility="internal",
            level="warning",
            source_type="agent",
        )

    def _resolve_requested_strategy(self, request: CreateRunRequest) -> str:
        if request.strategy:
            return str(request.strategy).strip().lower() or "simple"
        request_overrides = request.request_overrides or {}
        if isinstance(request_overrides, dict) and request_overrides.get("strategy"):
            return str(request_overrides.get("strategy")).strip().lower() or "simple"
        return "simple"


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.roster_manager import AgentRosterManager
    from run_manager import RunManager
