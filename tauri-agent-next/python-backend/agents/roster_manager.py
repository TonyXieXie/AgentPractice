from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agents.assistant import AssistantAgent
from agents.base import AgentBase
from agents.execution import ExecutionEngine, TaskManager, ToolExecutor
from agents.instance import AgentInstance
from agents.user_proxy import UserProxyAgent
from app_logging import log_debug, log_info
from repositories.agent_instance_repository import AgentInstanceRecord, AgentInstanceRepository
from repositories.agent_profile_repository import AgentProfileRepository


@dataclass(slots=True)
class RunRoster:
    session_id: str
    runtime_agents: Dict[str, AgentBase]


class AgentRosterManager:
    def __init__(
        self,
        *,
        agent_center: "AgentCenter",
        agent_instance_repository: AgentInstanceRepository,
        agent_profile_repository: AgentProfileRepository,
        memory: Any,
        tool_executor: ToolExecutor,
        task_manager: TaskManager,
    ) -> None:
        self.agent_center = agent_center
        self.agent_instance_repository = agent_instance_repository
        self.agent_profile_repository = agent_profile_repository
        self.memory = memory
        self.tool_executor = tool_executor
        self.task_manager = task_manager
        self.run_manager: Optional["RunManager"] = None
        self._session_agents: dict[str, dict[str, AgentBase]] = {}
        self._run_rosters: dict[str, RunRoster] = {}
        self._lock = asyncio.Lock()

    @property
    def default_profile_id(self) -> str:
        return self.agent_profile_repository.default_profile_id

    def bind_runtime_services(
        self,
        *,
        run_manager: "RunManager",
    ) -> None:
        self.run_manager = run_manager

    async def ensure_primary_user_proxy(self, session_id: str) -> UserProxyAgent:
        record = await self.agent_instance_repository.get_or_create_primary(
            session_id,
            "user_proxy",
            profile_id=None,
            display_name="UserProxy",
        )
        async with self._lock:
            session_agents = self._session_agents.setdefault(session_id, {})
            existing = session_agents.get(record.id)
            if isinstance(existing, UserProxyAgent):
                log_debug(
                    "roster.user_proxy.reused",
                    session_id=session_id,
                    agent_id=record.id,
                )
                return existing

        agent = self._instantiate_agent(record, run_id=None)
        if not isinstance(agent, UserProxyAgent):
            raise RuntimeError(f"Primary user_proxy runtime agent missing: {record.id}")

        async with self._lock:
            session_agents = self._session_agents.setdefault(session_id, {})
            existing = session_agents.get(record.id)
            if isinstance(existing, UserProxyAgent):
                log_debug(
                    "roster.user_proxy.reused_after_lock",
                    session_id=session_id,
                    agent_id=record.id,
                )
                return existing
            session_agents[record.id] = agent
        await self.agent_center.register(agent)
        log_info("roster.user_proxy.ready", session_id=session_id, agent_id=record.id)
        return agent

    async def ensure_primary_entry_assistant(self, session_id: str) -> AgentInstanceRecord:
        return await self.agent_instance_repository.get_or_create_primary(
            session_id,
            "assistant",
            profile_id=self.default_profile_id,
            display_name="Assistant",
        )

    async def hydrate_run_roster(self, session_id: str, run_id: str) -> Dict[str, AgentBase]:
        records = await self.agent_instance_repository.list_by_session(session_id)
        runtime_agents: Dict[str, AgentBase] = {}
        for record in records:
            if record.agent_type != "assistant":
                continue
            agent = self._instantiate_agent(record, run_id=run_id)
            runtime_agents[agent.agent_id] = agent

        async with self._lock:
            self._run_rosters[run_id] = RunRoster(
                session_id=session_id,
                runtime_agents=runtime_agents,
            )

        for agent in runtime_agents.values():
            await self.agent_center.register(agent)
        log_info(
            "roster.run_hydrated",
            session_id=session_id,
            run_id=run_id,
            assistant_count=len(runtime_agents),
        )
        return runtime_agents

    async def resolve_target_instance(self, message) -> AgentBase:
        target_id = _normalize_text(getattr(message, "target_id", None))
        if target_id:
            target = await self.agent_center.get(target_id)
            if target is None:
                raise ValueError(f"Target agent not found: {target_id}")
            return target

        profile_id = _extract_target_profile(message)
        if not profile_id:
            raise ValueError("target profile is required for profile routing")

        run_id = _normalize_text(getattr(message, "run_id", None))
        session_id = _extract_session_id(message)
        if not run_id or not session_id:
            raise ValueError("profile routing requires run_id and session_id")
        return await self.ensure_profile_instance(session_id, run_id, profile_id)

    async def ensure_profile_instance(
        self,
        session_id: str,
        run_id: str,
        profile_id: str,
    ) -> AgentBase:
        profile = await self.agent_profile_repository.get_required(profile_id)
        if profile.agent_type != "assistant":
            raise ValueError(
                f"Dynamic profile routing only supports assistant profiles: {profile_id}"
            )

        async with self._lock:
            roster = self._run_rosters.get(run_id)
            if roster is None or roster.session_id != session_id:
                raise ValueError(f"Active run roster not found: {run_id}")

            reusable = self._pick_reusable_agent(
                roster.runtime_agents,
                profile_id=profile.id,
            )
            if reusable is not None:
                return reusable

        record = await self.agent_instance_repository.create(
            session_id=session_id,
            agent_type=profile.agent_type,
            profile_id=profile.id,
            role=profile.agent_type,
            display_name=profile.display_name or profile.id,
            metadata={"profile_id": profile.id},
        )
        agent = self._instantiate_agent(record, run_id=run_id)
        await self.attach_runtime_agent(run_id, agent)
        log_info(
            "roster.profile_instance.created",
            session_id=session_id,
            run_id=run_id,
            profile_id=profile_id,
            agent_id=agent.agent_id,
        )
        return agent

    async def attach_runtime_agent(self, run_id: str, agent: AgentBase) -> None:
        async with self._lock:
            roster = self._run_rosters.get(run_id)
            if roster is None:
                raise ValueError(f"Active run roster not found: {run_id}")
            roster.runtime_agents[agent.agent_id] = agent
        await self.agent_center.register(agent)
        log_debug("roster.runtime_agent.attached", run_id=run_id, agent_id=agent.agent_id)

    async def list_broadcast_targets(self, message) -> list[AgentBase]:
        sender_id = _normalize_text(getattr(message, "sender_id", None))
        session_id = _extract_session_id(message)
        run_id = _normalize_text(getattr(message, "run_id", None))

        async with self._lock:
            candidates: dict[str, AgentBase] = {}
            if session_id:
                for agent in self._session_agents.get(session_id, {}).values():
                    candidates[agent.agent_id] = agent
            if run_id:
                roster = self._run_rosters.get(run_id)
                if roster is not None:
                    for agent in roster.runtime_agents.values():
                        candidates[agent.agent_id] = agent

        matched: list[AgentBase] = []
        for agent in candidates.values():
            if agent.agent_id == sender_id:
                continue
            if await self._is_subscribed(agent, getattr(message, "topic", None)):
                matched.append(agent)
        return matched

    async def teardown_run(self, run_id: str) -> None:
        async with self._lock:
            roster = self._run_rosters.pop(run_id, None)
        if roster is None:
            return
        for agent_id in list(roster.runtime_agents.keys()):
            await self.agent_center.unregister(agent_id)
        log_info(
            "roster.run_torn_down",
            run_id=run_id,
            session_id=roster.session_id,
            agent_count=len(roster.runtime_agents),
        )

    async def shutdown(self) -> None:
        async with self._lock:
            run_ids = list(self._run_rosters.keys())
            session_agents = {
                session_id: list(agents.keys())
                for session_id, agents in self._session_agents.items()
            }
            self._session_agents.clear()
        for run_id in run_ids:
            await self.teardown_run(run_id)
        for agents in session_agents.values():
            for agent_id in agents:
                await self.agent_center.unregister(agent_id)

    def _instantiate_agent(
        self,
        record: AgentInstanceRecord,
        *,
        run_id: Optional[str],
    ) -> AgentBase:
        profile_id = record.profile_id
        if record.agent_type == "assistant" and not _normalize_text(profile_id):
            profile_id = self.default_profile_id

        instance = AgentInstance(
            id=record.id,
            agent_type=record.agent_type,
            role=record.role,
            run_id=run_id,
            profile_id=profile_id,
            metadata=dict(record.metadata),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        if record.agent_type == "user_proxy":
            if self.run_manager is None:
                raise RuntimeError("UserProxyAgent requires bound runtime services")
            agent = UserProxyAgent(
                instance,
                self.agent_center,
                observer=self.agent_center.observer,
                run_manager=self.run_manager,
                roster_manager=self,
                task_manager=self.task_manager,
            )
            agent.execution_engine = ExecutionEngine(
                agent,
                memory=self.memory,
                tool_executor=self.tool_executor,
                strategies=agent.execution_engine._strategies,
            )
            return agent
        if record.agent_type == "assistant":
            agent = AssistantAgent(
                instance,
                self.agent_center,
                observer=self.agent_center.observer,
                task_manager=self.task_manager,
                profile_repository=self.agent_profile_repository,
            )
            agent.execution_engine = ExecutionEngine(
                agent,
                memory=self.memory,
                tool_executor=self.tool_executor,
                strategies=agent.execution_engine._strategies,
            )
            return agent
        raise RuntimeError(f"Unsupported agent_type for runtime instantiation: {record.agent_type}")

    async def _is_subscribed(self, agent: AgentBase, topic: object) -> bool:
        normalized_topic = _normalize_text(topic)
        if not normalized_topic:
            return False
        configured_topics = _normalize_topics(agent.instance.metadata.get("subscribed_topics"))
        if configured_topics:
            return normalized_topic in configured_topics
        if agent.instance.agent_type != "assistant":
            return False
        profile_id = _normalize_text(agent.instance.profile_id) or self.default_profile_id
        profile = await self.agent_profile_repository.get(profile_id)
        if profile is None:
            return False
        return profile.subscribes_to(normalized_topic)

    def _pick_reusable_agent(
        self,
        runtime_agents: Dict[str, AgentBase],
        *,
        profile_id: str,
    ) -> Optional[AgentBase]:
        candidates = [
            agent
            for agent in runtime_agents.values()
            if agent.instance.agent_type == "assistant"
            and self._assistant_profile_id(agent) == profile_id
        ]
        reusable = [
            agent
            for agent in candidates
            if agent.instance.status == "idle"
        ]
        if not reusable:
            return None
        reusable.sort(
            key=lambda agent: (
                str(agent.instance.created_at or ""),
                str(agent.instance.id or ""),
            )
        )
        return reusable[0]

    def _assistant_profile_id(self, agent: AgentBase) -> str:
        return _normalize_text(agent.instance.profile_id) or self.default_profile_id


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _extract_session_id(message) -> Optional[str]:
    session_id = _normalize_text(getattr(message, "session_id", None))
    if session_id:
        return session_id
    payload = getattr(message, "payload", None)
    if isinstance(payload, dict):
        session_id = _normalize_text(payload.get("session_id"))
        if session_id:
            return session_id
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        session_id = _normalize_text(metadata.get("session_id"))
        if session_id:
            return session_id
    return None


def _extract_target_profile(message) -> Optional[str]:
    target_profile = _normalize_text(getattr(message, "target_profile", None))
    if target_profile:
        return target_profile
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        target_profile = _normalize_text(metadata.get("target_profile"))
        if target_profile:
            return target_profile
    return None


def _normalize_topics(value: object) -> set[str]:
    if isinstance(value, str):
        text = _normalize_text(value)
        return {text} if text else set()
    if not isinstance(value, list):
        return set()
    return {text for item in value if (text := _normalize_text(item))}


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.center import AgentCenter
    from run_manager import RunManager
