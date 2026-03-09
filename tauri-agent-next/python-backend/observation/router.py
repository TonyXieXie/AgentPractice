from __future__ import annotations

from typing import Iterable, Optional

from observation.events import ExecutionEvent, level_at_least
from transport.ws.ws_types import SubscriptionScope, WSChunk


class SubscriptionRouter:
    def matches_event(self, scope: SubscriptionScope, event: ExecutionEvent) -> bool:
        if scope.run_id and scope.run_id != event.run_id:
            return False
        if scope.agent_id and scope.agent_id != event.agent_id:
            return False
        if scope.visibility and scope.visibility != event.visibility:
            return False
        if scope.level and not level_at_least(event.level, scope.level):
            return False
        return True

    def matches_chunk(self, scope: SubscriptionScope, chunk: WSChunk) -> bool:
        if scope.run_id and scope.run_id != chunk.run_id:
            return False
        if scope.agent_id and scope.agent_id != chunk.agent_id:
            return False
        if scope.visibility and scope.visibility != chunk.visibility:
            return False
        if scope.level and not level_at_least(chunk.level, scope.level):
            return False
        return True

    def matches_any_event(
        self,
        scopes: Iterable[SubscriptionScope],
        event: ExecutionEvent,
    ) -> bool:
        normalized = list(scopes) or [SubscriptionScope()]
        return any(self.matches_event(scope, event) for scope in normalized)

    def resolve_resume_run_id(self, scopes: Iterable[SubscriptionScope]) -> Optional[str]:
        normalized = list(scopes)
        if not normalized:
            return None
        run_ids = {scope.run_id for scope in normalized if scope.run_id}
        if not run_ids:
            return None
        if len(run_ids) != 1:
            return None
        if any(scope.run_id is None for scope in normalized):
            return None
        return next(iter(run_ids))
