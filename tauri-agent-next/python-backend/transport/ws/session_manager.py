from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import WebSocket

from app_logging import (
    LOG_CATEGORY_FRONTEND_BACKEND,
    log_debug,
    log_info,
    log_warning,
)
from observation.facts import ObservationScope, PrivateExecutionEvent, SharedFact
from observation.query_service import FactQueryService
from transport.ws.session import WsSession
from transport.ws.ws_types import (
    WsAppendPrivateEventFrame,
    WsAppendSharedFactFrame,
    WsBootstrapCursorsFrame,
    WsBootstrapPrivateEventsFrame,
    WsBootstrapSharedFactsFrame,
)


class WsSessionManager:
    def __init__(self, *, fact_query_service: FactQueryService) -> None:
        self.fact_query_service = fact_query_service
        self._sessions: dict[str, WsSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> WsSession:
        session = WsSession(websocket=websocket)
        async with self._lock:
            self._sessions[session.ws_session_id] = session
        log_info(
            "ws_session.registered",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
        )
        return session

    async def unregister(self, session: Optional[WsSession]) -> None:
        if session is None:
            return
        async with self._lock:
            self._sessions.pop(session.ws_session_id, None)
        log_info(
            "ws_session.unregistered",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
        )

    async def set_scope(
        self,
        session: WsSession,
        *,
        viewer_id: Optional[str],
        target_session_id: str,
        selected_run_id: Optional[str],
        selected_agent_id: Optional[str],
        include_private: bool,
    ) -> None:
        async with self._lock:
            existing = self._sessions.get(session.ws_session_id)
            if existing is None:
                return
            existing.viewer_id = viewer_id
            existing.target_session_id = target_session_id
            existing.selected_run_id = selected_run_id
            existing.selected_agent_id = selected_agent_id
            existing.include_private = include_private
            existing.shared_after_seq = 0
            existing.private_after_id = 0
        log_info(
            "ws_session.scope_set",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
            target_session_id=target_session_id,
            selected_run_id=selected_run_id,
            selected_agent_id=selected_agent_id,
            include_private=include_private,
        )

    async def request_bootstrap(
        self,
        session: WsSession,
        *,
        shared_limit: int,
        private_limit: int,
    ) -> tuple[int, int]:
        scope = self._scope_for_session(session)
        shared_facts = await self.fact_query_service.list_shared(
            scope,
            after_seq=0,
            limit=shared_limit,
        )
        private_events = await self.fact_query_service.list_private(
            scope,
            after_id=0,
            limit=private_limit,
        )
        shared_after_seq = shared_facts[-1].fact_seq if shared_facts else 0
        private_after_id = (
            private_events[-1].private_event_id if private_events else 0
        )
        session.shared_after_seq = max(session.shared_after_seq, shared_after_seq)
        session.private_after_id = max(session.private_after_id, private_after_id)
        await self._send(
            session,
            WsBootstrapSharedFactsFrame(
                ws_session_id=session.ws_session_id,
                shared_facts=[fact.model_dump(mode="json") for fact in shared_facts],
            ).model_dump(mode="json"),
        )
        await self._send(
            session,
            WsBootstrapPrivateEventsFrame(
                ws_session_id=session.ws_session_id,
                private_events=[event.model_dump(mode="json") for event in private_events],
            ).model_dump(mode="json"),
        )
        await self._send(
            session,
            WsBootstrapCursorsFrame(
                ws_session_id=session.ws_session_id,
                shared_after_seq=session.shared_after_seq,
                private_after_id=session.private_after_id,
            ).model_dump(mode="json"),
        )
        log_info(
            "ws_session.bootstrap_sent",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
            shared_count=len(shared_facts),
            private_count=len(private_events),
            shared_after_seq=session.shared_after_seq,
            private_after_id=session.private_after_id,
        )
        return session.shared_after_seq, session.private_after_id

    async def resume_shared(
        self,
        session: WsSession,
        *,
        after_seq: int,
        limit: int,
    ) -> int:
        scope = self._scope_for_session(session)
        shared_facts = await self.fact_query_service.list_shared(
            scope,
            after_seq=after_seq,
            limit=limit,
        )
        for fact in shared_facts:
            await self._send_shared_fact(session, fact)
        if shared_facts:
            session.shared_after_seq = max(
                session.shared_after_seq,
                shared_facts[-1].fact_seq,
            )
        log_debug(
            "ws_session.resume_shared.completed",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
            count=len(shared_facts),
            shared_after_seq=session.shared_after_seq,
        )
        return len(shared_facts)

    async def resume_private(
        self,
        session: WsSession,
        *,
        after_id: int,
        limit: int,
    ) -> int:
        scope = self._scope_for_session(session)
        private_events = await self.fact_query_service.list_private(
            scope,
            after_id=after_id,
            limit=limit,
        )
        for event in private_events:
            await self._send_private_event(session, event)
        if private_events:
            session.private_after_id = max(
                session.private_after_id,
                private_events[-1].private_event_id,
            )
        log_debug(
            "ws_session.resume_private.completed",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            ws_session_id=session.ws_session_id,
            count=len(private_events),
            private_after_id=session.private_after_id,
        )
        return len(private_events)

    async def publish_shared_fact(self, fact: SharedFact) -> int:
        sessions = await self._matching_sessions_for_shared(fact)
        delivered = 0
        for session in sessions:
            await self._send_shared_fact(session, fact)
            session.shared_after_seq = max(session.shared_after_seq, fact.fact_seq)
            delivered += 1
        log_debug(
            "ws_session.publish_shared.completed",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            fact_seq=fact.fact_seq,
            session_id=fact.session_id,
            delivered=delivered,
        )
        return delivered

    async def publish_private_event(self, event: PrivateExecutionEvent) -> int:
        sessions = await self._matching_sessions_for_private(event)
        delivered = 0
        for session in sessions:
            await self._send_private_event(session, event)
            session.private_after_id = max(session.private_after_id, event.private_event_id)
            delivered += 1
        log_debug(
            "ws_session.publish_private.completed",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            private_event_id=event.private_event_id,
            session_id=event.session_id,
            delivered=delivered,
        )
        return delivered

    def _scope_for_session(self, session: WsSession) -> ObservationScope:
        target_session_id = str(session.target_session_id or "").strip()
        if not target_session_id:
            raise RuntimeError("ws session scope is not set")
        return ObservationScope(
            session_id=target_session_id,
            run_id=session.selected_run_id,
            agent_id=session.selected_agent_id,
            include_private=session.include_private,
        )

    async def _matching_sessions_for_shared(self, fact: SharedFact) -> list[WsSession]:
        async with self._lock:
            sessions = list(self._sessions.values())
        return [
            session
            for session in sessions
            if session.target_session_id == fact.session_id
            and (
                not session.selected_run_id
                or session.selected_run_id == fact.run_id
            )
        ]

    async def _matching_sessions_for_private(
        self,
        event: PrivateExecutionEvent,
    ) -> list[WsSession]:
        async with self._lock:
            sessions = list(self._sessions.values())
        return [
            session
            for session in sessions
            if session.target_session_id == event.session_id
            and session.include_private
            and session.selected_agent_id == event.owner_agent_id
            and (
                not session.selected_run_id
                or session.selected_run_id == event.run_id
            )
        ]

    async def _send_shared_fact(self, session: WsSession, fact: SharedFact) -> None:
        await self._send(
            session,
            WsAppendSharedFactFrame(
                ws_session_id=session.ws_session_id,
                shared_fact=fact.model_dump(mode="json"),
            ).model_dump(mode="json"),
        )

    async def _send_private_event(
        self,
        session: WsSession,
        event: PrivateExecutionEvent,
    ) -> None:
        await self._send(
            session,
            WsAppendPrivateEventFrame(
                ws_session_id=session.ws_session_id,
                private_event=event.model_dump(mode="json"),
            ).model_dump(mode="json"),
        )

    async def _send(self, session: WsSession, payload: dict) -> None:
        try:
            await session.websocket.send_json(payload)
        except Exception:
            log_warning(
                "ws_session.send_failed",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                ws_session_id=session.ws_session_id,
            )
            await self.unregister(session)
