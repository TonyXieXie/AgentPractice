from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from agents.base import AgentBase
from agents.execution import (
    DirectiveRunner,
    ExecutionContext,
    ExecutionEngine,
    ReactStrategy,
    SimpleStrategy,
    TaskManager,
    ToolExecutor,
)
from agents.execution.message_utils import get_session_id, get_tool_name
from agents.execution.directives import RESERVED_DIRECTIVE_TOOL_NAMES
from agents.message import AgentMessage
from agents.profile import AgentProfile
from repositories.agent_profile_repository import AgentProfileRepository


RpcHandler = Callable[[AgentMessage], Awaitable[Dict[str, Any]] | Dict[str, Any] | Any]

ASSISTANT_SYSTEM_PROMPT = (
    "You are an assistant agent in a message-driven multi-agent runtime.\n"
    "- Every time you are awakened, choose exactly one control tool.\n"
    "- Your only allowed control tools are send_rpc_request, send_rpc_response, send_event, and broadcast_event.\n"
    "- You must not finish, fail, or stop the run directly.\n"
    "- Do not answer without selecting one of the allowed control tools.\n"
    "- Prefer explicit target delivery over broadcast unless broadcast is required.\n"
)


class AssistantAgent(AgentBase):
    def __init__(
        self,
        *args,
        task_handler: Optional[RpcHandler] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        task_manager: Optional[TaskManager] = None,
        profile_repository: Optional[AgentProfileRepository] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_handlers: Dict[str, RpcHandler] = {}
        self.received_events: list[AgentMessage] = []
        self.execution_engine = execution_engine or ExecutionEngine(
            self,
            strategies={
                "simple": SimpleStrategy(system_prompt=ASSISTANT_SYSTEM_PROMPT),
                "react": ReactStrategy(system_prompt=ASSISTANT_SYSTEM_PROMPT),
            },
        )
        self.task_manager = task_manager
        self.profile_repository = profile_repository
        self._resolved_profile_id: Optional[str] = None
        self._resolved_profile: Optional[AgentProfile] = None
        self.directive_runner = DirectiveRunner(
            self,
            run_manager=getattr(self.center, "run_manager", None),
            message_center_repository=getattr(self.center, "message_center_repository", None),
        )
        if task_handler is not None:
            self.register_rpc_handler("task.run", task_handler)

    def register_rpc_handler(self, topic: str, handler: RpcHandler) -> None:
        self._rpc_handlers[topic] = handler

    async def build_execution_context(self, base_tool_executor: ToolExecutor) -> ExecutionContext:
        profile = await self.get_execution_profile()
        filtered_tool_executor = base_tool_executor
        if profile is not None:
            filtered_tool_executor = base_tool_executor.clone(
                allowed_tool_names=profile.allowed_tool_names,
            )
        prompt_parts = [ASSISTANT_SYSTEM_PROMPT]
        if profile is not None and profile.system_prompt:
            prompt_parts.append(profile.system_prompt)
        tool_hint = _render_tool_availability_text(filtered_tool_executor)
        if tool_hint:
            prompt_parts.append(tool_hint)
        return ExecutionContext(
            resolved_profile=profile,
            system_prompt="\n\n".join(part for part in prompt_parts if part).strip(),
            tool_policy_text=str(getattr(profile, "tool_policy_text", "") or "").strip(),
            tool_executor=filtered_tool_executor,
        )

    async def get_execution_profile(self) -> Optional[AgentProfile]:
        if self.profile_repository is None:
            return None
        profile_id = str(
            self.instance.profile_id or self.profile_repository.default_profile_id or ""
        ).strip()
        if not profile_id:
            return None
        if self._resolved_profile_id == profile_id and self._resolved_profile is not None:
            return self._resolved_profile
        profile = await self.profile_repository.get_required(profile_id)
        self._resolved_profile_id = profile_id
        self._resolved_profile = profile
        return profile

    async def on_message(self, message: AgentMessage):
        if message.message_type == "event":
            self.received_events.append(message)
            await self._handle_execution_message(message)
            return None

        if message.message_type == "rpc" and message.rpc_phase == "response":
            await self._handle_execution_message(message)
            return None

        if message.message_type != "rpc" or message.rpc_phase != "request":
            return None

        if message.topic in self._rpc_handlers:
            await self.update_status("running", reason=message.topic)
            try:
                result_payload, ok = await self._execute_handler(
                    self._rpc_handlers[message.topic],
                    message,
                )
                await self.reply_rpc(
                    message,
                    result_payload,
                    ok=ok,
                    level="info" if ok else "error",
                    visibility="public" if ok else "internal",
                )
            except Exception as exc:
                await self.reply_rpc(
                    message,
                    {"error": str(exc)},
                    ok=False,
                    level="error",
                    visibility="internal",
                )
            finally:
                await self.update_status("idle", reason=message.topic)
            return None

        await self._handle_execution_message(message)
        return None

    async def _execute_handler(
        self,
        handler: RpcHandler,
        message: AgentMessage,
    ) -> tuple[Dict[str, Any], bool]:
        result = handler(message)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, tuple) and len(result) == 2:
            payload, ok = result
            if not isinstance(payload, dict):
                payload = {"result": payload}
            return payload, bool(ok)
        if not isinstance(result, dict):
            result = {"result": result}
        return result, True

    async def _handle_execution_message(self, message: AgentMessage) -> None:
        if not get_session_id(message):
            return
        if self.task_manager is None:
            raise RuntimeError("TaskManager is required for assistant execution handling")
        result = await self.task_manager.host_message_task(
            message,
            agent=self,
            execution_engine=self.execution_engine,
        )
        if result.protocol_violation is not None:
            await self._handle_protocol_violation(message, result)
            return
        await self.directive_runner.execute(message, result.directive)

    async def _handle_protocol_violation(self, message: AgentMessage, result) -> None:
        attempted_directive_kind = self._attempted_directive_kind(message, result)
        result_summary = self._result_summary(result)
        payload = {
            "offending_agent_id": self.agent_id,
            "source_message_id": message.id,
            "source_message_type": message.message_type,
            "source_rpc_phase": message.rpc_phase,
            "source_topic": message.topic,
            "reason": str(result.protocol_violation or "assistant protocol error"),
            "attempted_directive_kind": attempted_directive_kind,
            "result_summary": result_summary,
        }
        await self.observe(
            "assistant.protocol_error",
            payload=payload,
            run_id=message.run_id,
            message_id=message.id,
            visibility="internal",
            level="error",
        )
        if message.message_type == "rpc" and message.rpc_phase == "request":
            await self.reply_rpc(
                message,
                {
                    "error": payload["reason"],
                    "status": "protocol_error",
                    "attempted_directive_kind": attempted_directive_kind,
                    "result_summary": result_summary,
                },
                ok=False,
                level="error",
                visibility="internal",
            )
            return

        controller_agent_id = await self._resolve_controller_agent_id(message.run_id)
        if not controller_agent_id:
            return
        await self.send_event(
            "agent.protocol_error",
            payload,
            target_agent_id=controller_agent_id,
            run_id=message.run_id,
            session_id=message.session_id,
            visibility="internal",
            level="error",
        )

    async def _resolve_controller_agent_id(self, run_id: Optional[str]) -> Optional[str]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        run_manager = getattr(self.center, "run_manager", None)
        if run_manager is None:
            return None
        active_run = await run_manager.get_active_run(normalized_run_id)
        if active_run is None:
            return None
        return str(active_run.controller_agent_id or "").strip() or None

    def _attempted_directive_kind(self, message: AgentMessage, result) -> Optional[str]:
        if result.directive is not None:
            return result.directive.kind
        tool_name = get_tool_name(message)
        if tool_name in RESERVED_DIRECTIVE_TOOL_NAMES:
            return tool_name
        return None

    def _result_summary(self, result) -> str:
        return str(
            result.payload.get("reply")
            or result.payload.get("error")
            or (result.final_step.content if result.final_step is not None else "")
            or ""
        )


def _render_tool_availability_text(tool_executor: ToolExecutor) -> str:
    tool_names = [tool.name for tool in tool_executor.list_tools() if str(tool.name or "").strip()]
    if not tool_names:
        return "No tools are available for this profile."
    return (
        "Available tools for this profile: "
        + ", ".join(tool_names)
        + ". Do not call tools outside this set."
    )

