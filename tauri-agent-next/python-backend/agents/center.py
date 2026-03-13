from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import uuid4

from agents.message import AgentMessage
from observation.center import ObservationCenter
from observation.facts import SharedFact
from repositories.session_repository import SessionRepository


HTTP_INGRESS_SENDER_ID = "external:http"
AGENT_CENTER_SENDER_ID = "AgentCenter"


class AgentCenter:
    def __init__(
        self,
        observation_center: Optional[ObservationCenter] = None,
        *,
        roster_manager: Optional["AgentRosterManager"] = None,
        session_repository: Optional[SessionRepository] = None,
    ) -> None:
        self.observation_center = observation_center
        self.observer = observation_center
        self.shared_fact_repository = (
            observation_center.shared_fact_repository
            if observation_center is not None
            else None
        )
        self.roster_manager = roster_manager
        self.session_repository = session_repository
        self.run_manager: Optional["RunManager"] = None
        self._agents: Dict[str, "AgentBase"] = {}
        self._pending_rpcs: Dict[str, asyncio.Future[AgentMessage]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def register(self, agent: "AgentBase") -> None:
        async with self._lock:
            self._agents[agent.agent_id] = agent

    async def unregister(self, agent_id: str) -> None:
        async with self._lock:
            self._agents.pop(agent_id, None)

    async def get(self, agent_id: str) -> Optional["AgentBase"]:
        async with self._lock:
            return self._agents.get(agent_id)

    async def list_agents(self) -> List["AgentBase"]:
        async with self._lock:
            return list(self._agents.values())

    async def drain(self) -> None:
        while True:
            tasks = [task for task in self._background_tasks if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def dispatch_background(self, message: AgentMessage) -> None:
        self._spawn_background_task(self.route(message))

    async def expect_rpc_response(self, correlation_id: str) -> asyncio.Future[AgentMessage]:
        normalized = str(correlation_id or "").strip()
        if not normalized:
            raise ValueError("correlation_id is required")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        async with self._lock:
            existing = self._pending_rpcs.get(normalized)
            if existing is not None and not existing.done():
                raise RuntimeError(f"RPC waiter already exists: {normalized}")
            self._pending_rpcs[normalized] = future
        return future

    async def clear_rpc_waiter(
        self,
        correlation_id: str,
        future: Optional[asyncio.Future[AgentMessage]] = None,
    ) -> Optional[asyncio.Future[AgentMessage]]:
        normalized = str(correlation_id or "").strip()
        if not normalized:
            return None
        async with self._lock:
            existing = self._pending_rpcs.get(normalized)
            if existing is None:
                return None
            if future is not None and existing is not future:
                return None
            return self._pending_rpcs.pop(normalized, None)

    async def ingress_run_request(self, request: "CreateRunRequest") -> "CreateRunResponse":
        resolved_request = await self._resolve_run_request(request)
        roster_manager = self.roster_manager
        if roster_manager is None:
            raise RuntimeError("AgentRosterManager is required for run ingress")
        user_proxy = await roster_manager.ensure_primary_user_proxy(
            resolved_request.session_id or ""
        )
        response = await self._send_external_rpc(
            target_agent_id=user_proxy.agent_id,
            topic="run.submit",
            payload=resolved_request.model_dump(),
            session_id=resolved_request.session_id,
        )
        if not response.ok:
            status = str(response.payload.get("status") or "")
            if status == "busy":
                from run_manager import SessionBusyError

                raise SessionBusyError(
                    str(response.payload.get("session_id") or resolved_request.session_id or ""),
                    str(response.payload.get("active_run_id") or ""),
                )
            raise RuntimeError(str(response.payload.get("error") or "run submit failed"))
        from models import CreateRunAcceptedResponse

        return CreateRunAcceptedResponse.model_validate(response.payload)

    async def ingress_stop_request(self, run_id: str) -> Optional["StopRunResponse"]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        if self.run_manager is None:
            raise RuntimeError("RunManager is required for stop ingress")
        session_id = await self.run_manager.lookup_session_id(normalized_run_id)
        if not session_id:
            return None
        roster_manager = self.roster_manager
        if roster_manager is None:
            raise RuntimeError("AgentRosterManager is required for stop ingress")
        user_proxy = await roster_manager.ensure_primary_user_proxy(session_id)
        response = await self._send_external_rpc(
            target_agent_id=user_proxy.agent_id,
            topic="run.stop",
            payload={"run_id": normalized_run_id},
            run_id=normalized_run_id,
            session_id=session_id,
        )
        if not response.ok:
            status = str(response.payload.get("status") or "")
            if status == "not_found":
                return None
            raise RuntimeError(str(response.payload.get("error") or "run stop failed"))
        from models import StopRunResponse

        return StopRunResponse.model_validate(response.payload)

    async def route(self, message: AgentMessage):
        if message.object_type == "broadcast":
            shared_fact = await self._persist_shared_fact(message)
            routed_message = self._attach_trigger_fact(message, shared_fact)
            recipients = await self._resolve_broadcast_targets(routed_message)
            delivered = 0
            for agent in recipients:
                delivered_message = routed_message.model_copy(
                    update={"target": AgentMessage.TargetRef(agent_id=agent.agent_id)}
                )
                await agent.receive(delivered_message)
                delivered += 1
            return delivered

        if message.message_type == "rpc" and message.rpc_phase == "response":
            shared_fact = await self._persist_shared_fact(message)
            routed_message = self._attach_trigger_fact(message, shared_fact)
            future = await self._pop_pending_rpc(routed_message.correlation_id)
            if future is not None and not future.done():
                future.set_result(routed_message)
            target = await self.get(routed_message.target_id or "") if routed_message.target_id else None
            if target is not None:
                self._spawn_background_task(self._deliver_rpc_response(target, routed_message))
            elif future is None:
                await self._emit_undeliverable_rpc_response(routed_message)
            return routed_message

        target, routed_message = await self._resolve_target_message(message)
        shared_fact = await self._persist_shared_fact(routed_message)
        routed_message = self._attach_trigger_fact(routed_message, shared_fact)

        if routed_message.message_type == "rpc" and routed_message.rpc_phase == "request":
            self._spawn_background_task(self._deliver_rpc_request(target, routed_message))
            return routed_message

        return await target.receive(routed_message)

    async def _resolve_run_request(self, request: "CreateRunRequest") -> "CreateRunRequest":
        session_repo = self.session_repository
        if session_repo is None:
            raise RuntimeError("SessionRepository is required for run ingress")
        if request.session_id:
            from run_manager import SessionNotFoundError

            session = await session_repo.get(request.session_id)
            if session is None:
                raise SessionNotFoundError(request.session_id)
            if (
                request.system_prompt is not None
                or request.work_path is not None
                or request.llm_config is not None
            ):
                session = await session_repo.update_defaults(
                    request.session_id,
                    system_prompt=request.system_prompt,
                    work_path=request.work_path,
                    llm_config=request.llm_config,
                )
            effective_llm_config = (
                request.llm_config
                if request.llm_config is not None
                else (session.llm_config if session else None)
            )
            effective_system_prompt = (
                request.system_prompt
                if request.system_prompt is not None
                else (session.system_prompt if session else None)
            )
            effective_work_path = (
                request.work_path
                if request.work_path is not None
                else (session.work_path if session else None)
            )
            return request.model_copy(
                update={
                    "session_id": request.session_id,
                    "llm_config": effective_llm_config,
                    "system_prompt": effective_system_prompt,
                    "work_path": effective_work_path,
                }
            )

        session_id = uuid4().hex
        created = await session_repo.create(
            session_id=session_id,
            system_prompt=request.system_prompt,
            work_path=request.work_path,
            llm_config=request.llm_config,
        )
        return request.model_copy(
            update={
                "session_id": session_id,
                "llm_config": created.llm_config,
                "system_prompt": created.system_prompt,
                "work_path": created.work_path,
            }
        )

    async def _send_external_rpc(
        self,
        *,
        target_agent_id: str,
        topic: str,
        payload: dict,
        session_id: Optional[str],
        run_id: Optional[str] = None,
    ) -> AgentMessage:
        message = AgentMessage.build_rpc_request(
            topic=topic,
            sender_id=HTTP_INGRESS_SENDER_ID,
            target_id=target_agent_id,
            payload=payload,
            run_id=run_id,
            session_id=session_id,
            visibility="internal",
        )
        correlation_id = message.correlation_id or message.id
        future = await self.expect_rpc_response(correlation_id)
        try:
            await self.route(message)
            return await asyncio.wait_for(future, timeout=30.0)
        finally:
            await self.clear_rpc_waiter(correlation_id, future)

    async def _resolve_broadcast_targets(self, message: AgentMessage) -> List["AgentBase"]:
        if self.roster_manager is not None:
            return await self.roster_manager.list_broadcast_targets(message)
        sender_id = message.sender_id
        async with self._lock:
            return [agent for agent_id, agent in self._agents.items() if agent_id != sender_id]

    async def _resolve_target_message(
        self,
        message: AgentMessage,
    ) -> tuple["AgentBase", AgentMessage]:
        target_id = str(message.target_id or "").strip()
        if target_id:
            target = await self.get(target_id)
            if target is None:
                raise ValueError(f"Target agent not found: {message.target_id}")
            return target, message

        if self.roster_manager is None:
            raise ValueError("profile routing requires AgentRosterManager")

        original_target_profile = _extract_target_profile(message)
        target = await self.roster_manager.resolve_target_instance(message)
        metadata = dict(message.metadata or {})
        if original_target_profile:
            metadata["target_profile"] = original_target_profile
        routed_message = message.model_copy(
            update={
                "target": AgentMessage.TargetRef(agent_id=target.agent_id),
                "metadata": metadata,
            }
        )
        return target, routed_message

    async def _persist_shared_fact(self, message: AgentMessage) -> SharedFact:
        observation_center = self.observation_center
        session_id = _extract_session_id(message)
        if observation_center is None or not session_id:
            return SharedFact(
                session_id=session_id or "",
                run_id=message.run_id,
                message_id=message.id,
                sender_id=message.sender_id,
                target_agent_id=message.target_id,
                target_profile_id=_extract_target_profile(message),
                topic=message.topic,
                fact_type=_fact_type_for_message(message),
                payload_json=dict(message.payload or {}),
                metadata_json=_metadata_for_shared_fact(message),
                visibility=message.visibility,
                level=message.level,
            )
        return await observation_center.append_shared_fact(
            session_id=session_id,
            run_id=message.run_id,
            message_id=message.id,
            sender_id=message.sender_id,
            target_agent_id=message.target_id,
            target_profile_id=_extract_target_profile(message),
            topic=message.topic,
            fact_type=_fact_type_for_message(message),
            payload_json=dict(message.payload or {}),
            metadata_json=_metadata_for_shared_fact(message),
            visibility=message.visibility,
            level=message.level,
        )

    def _attach_trigger_fact(
        self,
        message: AgentMessage,
        fact: SharedFact,
    ) -> AgentMessage:
        metadata = dict(message.metadata or {})
        if fact.fact_id:
            metadata["trigger_fact_id"] = fact.fact_id
        return message.model_copy(
            update={
                "seq": fact.fact_seq or message.seq,
                "metadata": metadata,
            }
        )

    async def _pop_pending_rpc(
        self,
        correlation_id: Optional[str],
    ) -> Optional[asyncio.Future[AgentMessage]]:
        if not correlation_id:
            return None
        async with self._lock:
            return self._pending_rpcs.pop(correlation_id, None)

    def _spawn_background_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _deliver_rpc_request(self, target: "AgentBase", message: AgentMessage) -> None:
        try:
            maybe_response = await target.receive(message)
            if isinstance(maybe_response, AgentMessage):
                await self.route(maybe_response)
        except Exception as exc:
            error_response = AgentMessage.build_rpc_response(
                request=message,
                sender_id=target.agent_id,
                payload={"error": str(exc)},
                ok=False,
                visibility="internal",
                level="error",
            )
            await self.route(error_response)

    async def _deliver_rpc_response(self, target: "AgentBase", message: AgentMessage) -> None:
        try:
            maybe_followup = await target.receive(message)
            if isinstance(maybe_followup, AgentMessage):
                await self.route(maybe_followup)
        except Exception as exc:
            await self._append_center_error_fact(
                message,
                topic="message.delivery_error",
                payload={
                    "topic": message.topic,
                    "message_type": message.message_type,
                    "rpc_phase": message.rpc_phase,
                    "error": str(exc),
                    "target_agent_id": target.agent_id,
                },
            )

    async def _emit_undeliverable_rpc_response(self, message: AgentMessage) -> None:
        await self._append_center_error_fact(
            message,
            topic="message.undeliverable",
            payload={
                "topic": message.topic,
                "target_agent_id": message.target_id,
                "correlation_id": message.correlation_id,
            },
        )

    async def _append_center_error_fact(
        self,
        message: AgentMessage,
        *,
        topic: str,
        payload: dict,
    ) -> None:
        session_id = _extract_session_id(message)
        if self.observation_center is None or not session_id:
            return
        await self.observation_center.append_shared_fact(
            session_id=session_id,
            run_id=message.run_id,
            sender_id=AGENT_CENTER_SENDER_ID,
            target_agent_id=message.target_id,
            target_profile_id=_extract_target_profile(message),
            topic=topic,
            fact_type="event_handoff",
            payload_json=payload,
            metadata_json={"source_message_id": message.id},
            visibility="internal",
            level="error",
        )


def _extract_session_id(message: AgentMessage) -> Optional[str]:
    if message.session_id:
        return str(message.session_id)
    payload = message.payload or {}
    if isinstance(payload, dict) and payload.get("session_id"):
        return str(payload.get("session_id"))
    metadata = message.metadata or {}
    if isinstance(metadata, dict) and metadata.get("session_id"):
        return str(metadata.get("session_id"))
    return None


def _extract_target_profile(message: AgentMessage) -> Optional[str]:
    value = message.target_profile
    if value:
        return str(value)
    metadata = message.metadata or {}
    if isinstance(metadata, dict) and metadata.get("target_profile"):
        return str(metadata.get("target_profile"))
    return None


def _fact_type_for_message(message: AgentMessage) -> str:
    if message.message_type == "event":
        return "event_handoff"
    if message.rpc_phase == "response":
        return "rpc_response"
    return "rpc_request"


def _metadata_for_shared_fact(message: AgentMessage) -> dict:
    metadata = dict(message.metadata or {})
    metadata.update(
        {
            "message_type": message.message_type,
            "object_type": message.object_type,
            "rpc_phase": message.rpc_phase,
            "correlation_id": message.correlation_id,
            "ok": message.ok,
        }
    )
    return metadata


if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.roster_manager import AgentRosterManager
    from models import CreateRunRequest, CreateRunResponse, StopRunResponse
    from run_manager import RunManager
