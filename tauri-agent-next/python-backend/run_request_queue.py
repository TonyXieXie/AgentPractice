from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional
from uuid import uuid4

from agents.message import utc_now_iso
from models import CreateRunRequest, RunTicketResponse


@dataclass(slots=True)
class RunRequestTicket:
    ticket_id: str
    session_id: str
    request: CreateRunRequest
    status: str
    run_id: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str

    def to_response(self) -> RunTicketResponse:
        return RunTicketResponse(
            ticket_id=self.ticket_id,
            session_id=self.session_id,
            status=self.status,
            run_id=self.run_id,
            error=self.error,
        )


class RunRequestQueue:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tickets: Dict[str, RunRequestTicket] = {}
        self._session_queues: Dict[str, Deque[str]] = defaultdict(deque)

    async def enqueue(self, *, session_id: str, request: CreateRunRequest) -> RunRequestTicket:
        ticket = RunRequestTicket(
            ticket_id=uuid4().hex,
            session_id=session_id,
            request=request.model_copy(deep=True),
            status="queued",
            run_id=None,
            error=None,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        async with self._lock:
            self._tickets[ticket.ticket_id] = ticket
            self._session_queues[session_id].append(ticket.ticket_id)
        return ticket

    async def get(self, ticket_id: str) -> Optional[RunRequestTicket]:
        async with self._lock:
            return self._tickets.get(ticket_id)

    async def pop_next(self, session_id: str) -> Optional[RunRequestTicket]:
        async with self._lock:
            queue = self._session_queues.get(session_id)
            if not queue:
                return None
            while queue:
                ticket_id = queue.popleft()
                ticket = self._tickets.get(ticket_id)
                if ticket is None:
                    continue
                if ticket.status != "queued":
                    continue
                return ticket
            self._session_queues.pop(session_id, None)
            return None

    async def mark_started(self, ticket_id: str, *, run_id: str) -> Optional[RunRequestTicket]:
        async with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                return None
            ticket.status = "started"
            ticket.run_id = run_id
            ticket.updated_at = utc_now_iso()
            return ticket

    async def mark_rejected(self, ticket_id: str, *, error: str) -> Optional[RunRequestTicket]:
        async with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                return None
            ticket.status = "rejected"
            ticket.error = str(error)
            ticket.updated_at = utc_now_iso()
            return ticket
