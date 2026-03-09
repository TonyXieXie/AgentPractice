from __future__ import annotations

from observation.events import (
    AgentProjection,
    ExecutionProjectionState,
    ExecutionSnapshot,
    RunProjection,
    ToolCallProjection,
)


class ProjectionBuilder:
    def build(self, snapshot: ExecutionSnapshot) -> ExecutionProjectionState:
        if not snapshot.run_id:
            return ExecutionProjectionState()

        run_projection = RunProjection(
            run_id=snapshot.run_id,
            status=snapshot.status,
            latest_seq=snapshot.latest_seq,
            strategy=snapshot.metadata.get("strategy"),
            reply=snapshot.metadata.get("reply"),
            error=snapshot.metadata.get("error"),
            started_at=snapshot.metadata.get("started_at"),
            finished_at=snapshot.metadata.get("finished_at"),
            updated_at=snapshot.updated_at,
            metadata=dict(snapshot.metadata),
        )
        agent_projections = {
            agent_id: AgentProjection(
                run_id=snapshot.run_id,
                agent_id=agent_id,
                status=agent_snapshot.status,
                role=agent_snapshot.role,
                updated_at=agent_snapshot.updated_at,
                metadata=dict(agent_snapshot.metadata),
            )
            for agent_id, agent_snapshot in snapshot.agents.items()
        }
        tool_call_projections = {
            tool_call_id: ToolCallProjection(
                run_id=snapshot.run_id,
                tool_call_id=tool_call_id,
                status=tool_snapshot.status,
                agent_id=tool_snapshot.agent_id,
                tool_name=tool_snapshot.tool_name,
                updated_at=tool_snapshot.updated_at,
                output=_optional_text(tool_snapshot.payload.get("content")),
                error=_optional_text(tool_snapshot.payload.get("error")),
                payload=dict(tool_snapshot.payload),
            )
            for tool_call_id, tool_snapshot in snapshot.tool_calls.items()
        }
        return ExecutionProjectionState(
            run_projection=run_projection,
            agent_projections=agent_projections,
            tool_call_projections=tool_call_projections,
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
