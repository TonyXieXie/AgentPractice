from __future__ import annotations

from typing import Optional

from app_logging import log_debug
from observation.facts import ObservationScope, PrivateExecutionEvent, SharedFact
from observation.query_service import FactQueryService
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.shared_fact_repository import SharedFactRepository

if False:
    from transport.ws.session_manager import WsSessionManager


class ObservationCenter:
    def __init__(
        self,
        *,
        shared_fact_repository: SharedFactRepository,
        agent_private_event_repository: AgentPrivateEventRepository,
        fact_query_service: Optional[FactQueryService] = None,
        ws_session_manager: Optional["WsSessionManager"] = None,
    ) -> None:
        self.shared_fact_repository = shared_fact_repository
        self.agent_private_event_repository = agent_private_event_repository
        self.fact_query_service = fact_query_service or FactQueryService(
            shared_fact_repository=shared_fact_repository,
            agent_private_event_repository=agent_private_event_repository,
        )
        self.ws_session_manager = ws_session_manager

    async def append_shared_fact(
        self,
        *,
        session_id: str,
        sender_id: str,
        topic: str,
        fact_type: str,
        payload_json: dict,
        metadata_json: Optional[dict] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        target_profile_id: Optional[str] = None,
        visibility: str = "public",
        level: str = "info",
    ) -> SharedFact:
        log_debug(
            "observation.shared_fact.append",
            session_id=session_id,
            run_id=run_id,
            sender_id=sender_id,
            target_agent_id=target_agent_id,
            topic=topic,
            fact_type=fact_type,
            level=level,
        )
        fact = await self.shared_fact_repository.append(
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            sender_id=sender_id,
            target_agent_id=target_agent_id,
            target_profile_id=target_profile_id,
            topic=topic,
            fact_type=fact_type,
            payload_json=payload_json,
            metadata_json=metadata_json or {},
            visibility=visibility,
            level=level,
        )
        if self.ws_session_manager is not None:
            await self.ws_session_manager.publish_shared_fact(fact)
        return fact

    async def append_private_event(
        self,
        *,
        session_id: str,
        owner_agent_id: str,
        kind: str,
        payload_json: dict,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        trigger_fact_id: Optional[str] = None,
        parent_private_event_id: Optional[int] = None,
    ) -> PrivateExecutionEvent:
        log_debug(
            "observation.private_event.append",
            session_id=session_id,
            run_id=run_id,
            owner_agent_id=owner_agent_id,
            task_id=task_id,
            kind=kind,
        )
        event = await self.agent_private_event_repository.append(
            session_id=session_id,
            owner_agent_id=owner_agent_id,
            run_id=run_id,
            task_id=task_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            trigger_fact_id=trigger_fact_id,
            parent_private_event_id=parent_private_event_id,
            kind=kind,
            payload_json=payload_json,
        )
        if self.ws_session_manager is not None:
            await self.ws_session_manager.publish_private_event(event)
        return event

    async def list_shared(
        self,
        scope: ObservationScope,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[SharedFact]:
        return await self.fact_query_service.list_shared(
            scope,
            after_seq=after_seq,
            limit=limit,
        )

    async def list_private(
        self,
        scope: ObservationScope,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> list[PrivateExecutionEvent]:
        return await self.fact_query_service.list_private(
            scope,
            after_id=after_id,
            limit=limit,
        )
