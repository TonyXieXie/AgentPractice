from __future__ import annotations

from typing import Any, Dict

from observation.events import AgentSnapshot, ExecutionEvent, ExecutionSnapshot, ToolCallSnapshot


class SnapshotBuilder:
    def apply(
        self,
        snapshot: ExecutionSnapshot,
        event: ExecutionEvent,
    ) -> ExecutionSnapshot:
        run_metadata = dict(snapshot.metadata)
        status = snapshot.status
        if event.event_type == "run.started":
            status = "running"
            run_metadata["started_at"] = run_metadata.get("started_at") or event.created_at
        elif event.event_type == "run.finished":
            status = str(event.payload.get("status") or "finished")
            run_metadata["finished_at"] = event.created_at
        elif event.event_type == "run.error":
            status = "error"
            run_metadata["error"] = str(
                event.payload.get("error")
                or event.payload.get("content")
                or event.payload.get("message")
                or "run error"
            )
        if "strategy" in event.payload:
            run_metadata["strategy"] = event.payload.get("strategy")
        if "reply" in event.payload:
            run_metadata["reply"] = event.payload.get("reply")
        if event.payload.get("status") == "stopped":
            status = "stopped"
            run_metadata["finished_at"] = event.created_at

        update_payload: Dict[str, Any] = {
            "latest_seq": max(snapshot.latest_seq, int(event.seq or 0)),
            "latest_event_type": event.event_type,
            "updated_at": event.created_at,
            "metadata": run_metadata,
            "status": status,
        }
        if event.run_id and not snapshot.run_id:
            update_payload["run_id"] = event.run_id

        next_snapshot = snapshot.model_copy(update=update_payload, deep=True)

        if event.agent_id:
            agents = dict(next_snapshot.agents)
            current = agents.get(event.agent_id) or AgentSnapshot(
                agent_id=event.agent_id,
                status="idle",
            )
            merged_metadata = dict(current.metadata)
            if event.event_type == "agent.state_changed":
                merged_metadata.update(event.payload)
            else:
                merged_metadata["last_event_type"] = event.event_type
                if event.message_id:
                    merged_metadata["last_message_id"] = event.message_id
            agents[event.agent_id] = current.model_copy(
                update={
                    "status": str(event.payload.get("status") or current.status),
                    "role": event.payload.get("role") or current.role,
                    "updated_at": event.created_at,
                    "metadata": merged_metadata,
                }
            )
            next_snapshot = next_snapshot.model_copy(update={"agents": agents}, deep=True)

        if event.tool_call_id:
            tool_calls = dict(next_snapshot.tool_calls)
            current_tool = tool_calls.get(event.tool_call_id) or ToolCallSnapshot(
                tool_call_id=event.tool_call_id,
                run_id=event.run_id,
                status="pending",
                agent_id=event.agent_id,
            )
            merged_payload = dict(current_tool.payload)
            merged_payload.update(event.payload)
            tool_calls[event.tool_call_id] = current_tool.model_copy(
                update={
                    "status": str(event.payload.get("status") or current_tool.status),
                    "tool_name": event.payload.get("tool_name") or current_tool.tool_name,
                    "agent_id": event.agent_id or current_tool.agent_id,
                    "run_id": event.run_id or current_tool.run_id,
                    "updated_at": event.created_at,
                    "payload": merged_payload,
                }
            )
            next_snapshot = next_snapshot.model_copy(update={"tool_calls": tool_calls}, deep=True)

        return next_snapshot
