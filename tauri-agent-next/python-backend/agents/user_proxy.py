from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.base import AgentBase
from agents.execution.message_utils import get_session_id
from agents.message import AgentMessage
from models import CreateRunAcceptedResponse, CreateRunQueuedResponse, CreateRunRequest


class UserProxyAgent(AgentBase):
    def __init__(
        self,
        *args,
        run_manager: "RunManager",
        roster_manager: "AgentRosterManager",
        run_request_queue: "RunRequestQueue",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.run_manager = run_manager
        self.roster_manager = roster_manager
        self.run_request_queue = run_request_queue
        self.received_events: list[AgentMessage] = []

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
        return await self.call_rpc(
            topic,
            payload,
            target_agent_id=target_agent_id,
            run_id=run_id or self.run_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def on_message(self, message: AgentMessage):
        if message.message_type == "event":
            self.received_events.append(message)
            if message.topic == "run.dequeue":
                await self._handle_run_dequeue(message)
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
            ticket = await self.run_request_queue.enqueue(
                session_id=session_id,
                request=request,
            )
            await self.reply_rpc(
                message,
                CreateRunQueuedResponse(
                    ticket_id=ticket.ticket_id,
                    session_id=session_id,
                ).model_dump(),
                ok=True,
            )
            return

        try:
            response = await self._start_run(request)
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
        await self.reply_rpc(message, response.model_dump(), ok=True)

    async def _handle_run_dequeue(self, message: AgentMessage) -> None:
        session_id = get_session_id(message)
        if not session_id:
            return
        while await self.run_manager.get_active_run_by_session(session_id) is None:
            ticket = await self.run_request_queue.pop_next(session_id)
            if ticket is None:
                return
            try:
                response = await self._start_run(ticket.request)
            except Exception as exc:
                await self.run_request_queue.mark_rejected(ticket.ticket_id, error=str(exc))
                continue
            await self.run_request_queue.mark_started(ticket.ticket_id, run_id=response.run_id)
            return

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
        try:
            root_task = asyncio.create_task(
                self._execute_run(
                    run_id=active_run.run_id,
                    session_id=session_id,
                    assistant_agent_id=assistant_record.id,
                    request=request,
                ),
                name=f"run-root:{active_run.run_id}",
            )
            await self.run_manager.attach_root_task(active_run.run_id, root_task)
        except Exception as exc:
            await self.run_manager.fail_run(active_run.run_id, str(exc))
            raise

        return CreateRunAcceptedResponse(
            run_id=active_run.run_id,
            session_id=session_id,
            user_agent_id=self.agent_id,
            assistant_agent_id=assistant_record.id,
        )

    async def _execute_run(
        self,
        *,
        run_id: str,
        session_id: str,
        assistant_agent_id: str,
        request: CreateRunRequest,
    ) -> None:
        try:
            response = await self.send_user_message(
                request.content,
                target_agent_id=assistant_agent_id,
                run_id=run_id,
                session_id=session_id,
                strategy=request.strategy,
                history=request.history,
                llm_config=request.llm_config,
                system_prompt=request.system_prompt,
                work_path=request.work_path,
                request_overrides=request.request_overrides,
            )
            if response.ok:
                await self.run_manager.finish_run(
                    run_id,
                    response.payload,
                    message_id=response.correlation_id or response.id,
                )
            else:
                error_text = str(
                    response.payload.get("error")
                    or response.payload.get("reply")
                    or "run failed"
                )
                await self.run_manager.fail_run(
                    run_id,
                    error_text,
                    message_id=response.correlation_id or response.id,
                    result_payload=response.payload,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.run_manager.fail_run(run_id, str(exc))
        finally:
            try:
                await self.send_event(
                    "run.dequeue",
                    {"session_id": session_id},
                    target_agent_id=self.agent_id,
                    session_id=session_id,
                    visibility="internal",
                    level="debug",
                    metadata={"session_id": session_id},
                )
            except Exception:
                return

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
    from run_request_queue import RunRequestQueue
