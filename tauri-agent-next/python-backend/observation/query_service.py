from __future__ import annotations

from observation.facts import ObservationScope, PrivateExecutionEvent, SharedFact
from repositories.agent_private_event_repository import AgentPrivateEventRepository
from repositories.shared_fact_repository import SharedFactRepository


class FactQueryService:
    def __init__(
        self,
        *,
        shared_fact_repository: SharedFactRepository,
        agent_private_event_repository: AgentPrivateEventRepository,
    ) -> None:
        self.shared_fact_repository = shared_fact_repository
        self.agent_private_event_repository = agent_private_event_repository

    async def list_shared(
        self,
        scope: ObservationScope,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[SharedFact]:
        return await self.shared_fact_repository.list(
            scope.session_id,
            after_seq=after_seq,
            limit=limit,
            run_id=scope.run_id,
        )

    async def list_private(
        self,
        scope: ObservationScope,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> list[PrivateExecutionEvent]:
        if not scope.agent_id:
            return []
        return await self.agent_private_event_repository.list(
            scope.session_id,
            owner_agent_id=scope.agent_id,
            run_id=scope.run_id,
            after_id=after_id,
            limit=limit,
        )

    async def get_latest_run_status(self, run_id: str) -> str | None:
        fact = await self.shared_fact_repository.get_latest_run_status_fact(run_id)
        if fact is None:
            return None
        payload = fact.payload
        status = str(payload.get("status") or "").strip()
        return status or None
