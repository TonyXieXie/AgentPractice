from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from agents.base import AgentBase
from agents.execution import (
    DirectiveRunner,
    ExecutionContext,
    ExecutionEngine,
    ReactStrategy,
    SimpleStrategy,
    TaskManager,
)
from agents.execution.directives import RESERVED_DIRECTIVE_TOOL_NAMES
from agents.execution.handoff_support import (
    render_handoff_catalog_text,
    render_tool_availability_text,
    resolve_handoff_target,
)
from agents.execution.message_utils import get_session_id, get_tool_name
from agents.message import AgentMessage
from app_logging import (
    LOG_CATEGORY_FRONTEND_BACKEND,
    log_error,
    log_info,
    log_warning,
)
from models import CreateRunAcceptedResponse, CreateRunRequest


USER_PROXY_SYSTEM_PROMPT = (
    "You are the run controller UserProxyAgent.\n"
    "- You are the final judge of whether the run should continue, finish, fail, or stop.\n"
    "- Do not finish a run only because you received one rpc response; inspect the shared facts first.\n"
    "- Every routed message is a shared fact. Use the shared history to understand the full session state.\n"
    "- Act only by choosing exactly one control tool: handoff, finish_run, fail_run, or stop_run.\n"
    "- Low-level RPC and event routing are handled by the backend.\n"
    "- Keep run.stop command-style behavior intact when asked to stop.\n"
    "- Use handoff when another assistant profile should take over execution.\n"
)


class UserProxyAgent(AgentBase):
    def __init__(
        self,
        *args,
        run_manager: "RunManager",
        roster_manager: "AgentRosterManager",
        execution_engine: Optional[ExecutionEngine] = None,
        task_manager: Optional[TaskManager] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.run_manager = run_manager
        self.roster_manager = roster_manager
        self.task_manager = task_manager
        self.received_events: list[AgentMessage] = []
        self.execution_engine = execution_engine or ExecutionEngine(
            self,
            strategies={
                "simple": SimpleStrategy(system_prompt=USER_PROXY_SYSTEM_PROMPT),
                "react": ReactStrategy(system_prompt=USER_PROXY_SYSTEM_PROMPT),
            },
        )
        self.directive_runner = DirectiveRunner(
            self,
            run_manager=self.run_manager,
            profile_repository=self.roster_manager.agent_profile_repository,
            shared_fact_repository=getattr(self.center, "shared_fact_repository", None),
        )

    async def build_execution_context(self, base_tool_executor) -> ExecutionContext:
        prompt_parts = [USER_PROXY_SYSTEM_PROMPT]
        tool_hint = render_tool_availability_text(base_tool_executor)
        if tool_hint:
            prompt_parts.append(tool_hint)
        if base_tool_executor.get_tool("handoff") is not None:
            handoff_hint = await render_handoff_catalog_text(
                self.roster_manager.agent_profile_repository,
            )
            if handoff_hint:
                prompt_parts.append(handoff_hint)
        return ExecutionContext(
            system_prompt="\n\n".join(part for part in prompt_parts if part).strip(),
            tool_executor=base_tool_executor,
        )

    async def send_user_message(
        self,
        content: str,
        *,
        target_agent_id: str,
        topic: str = "task.run",
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        strategy: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        payload_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        message = self._build_user_message_request(
            content,
            target_agent_id=target_agent_id,
            topic=topic,
            run_id=run_id,
            session_id=session_id,
            strategy=strategy,
            history=history,
            llm_config=llm_config,
            system_prompt=system_prompt,
            work_path=work_path,
            request_overrides=request_overrides,
            payload_overrides=payload_overrides,
            metadata=metadata,
        )
        await self.center.route(message)
        return message

    def _build_user_message_request(
        self,
        content: str,
        *,
        target_agent_id: str,
        topic: str = "task.run",
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        strategy: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        work_path: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        payload_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        payload: Dict[str, Any] = {"content": content}
        if strategy:
            payload["strategy"] = strategy
        if session_id is not None:
            payload["session_id"] = session_id
        if history is not None:
            payload["history"] = deepcopy(history)
        if llm_config is not None:
            payload["llm_config"] = deepcopy(llm_config)
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if work_path is not None:
            payload["work_path"] = work_path
        if request_overrides is not None:
            payload["request_overrides"] = deepcopy(request_overrides)
        if payload_overrides:
            payload.update(deepcopy(payload_overrides))
        return self._build_outbound_rpc_request(
            topic=topic,
            payload=payload,
            target_agent_id=target_agent_id,
            run_id=run_id or self.run_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def on_message(self, message: AgentMessage):
        if message.message_type == "rpc" and message.rpc_phase == "request":
            if message.topic == "run.submit":
                await self._handle_run_submit(message)
                return None
            if message.topic == "run.stop":
                await self._handle_run_stop(message)
                return None

        if message.message_type == "event":
            self.received_events.append(message)

        await self._handle_controller_message(message)
        return None

    async def _handle_run_submit(self, message: AgentMessage) -> None:
        from run_manager import SessionBusyError

        request = CreateRunRequest.model_validate(dict(message.payload or {}))
        session_id = get_session_id(message) or request.session_id
        log_info(
            "user_proxy.run_submit.received",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            session_id=session_id,
            request_id=message.id,
        )
        if not session_id:
            log_warning(
                "user_proxy.run_submit.rejected",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                reason="missing_session_id",
            )
            await self.reply_rpc(
                message,
                {"error": "session_id is required", "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        request = request.model_copy(update={"session_id": session_id})

        active_run = await self.run_manager.get_active_run_by_session(session_id)
        if active_run is not None:
            log_warning(
                "user_proxy.run_submit.busy",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                session_id=session_id,
                active_run_id=active_run.run_id,
            )
            await self.reply_rpc(
                message,
                {
                    "error": f"session busy: {session_id}",
                    "status": "busy",
                    "session_id": session_id,
                    "active_run_id": active_run.run_id,
                },
                ok=False,
                level="error",
                visibility="internal",
            )
            return

        try:
            response = await self._start_run(request)
        except SessionBusyError as exc:
            log_warning(
                "user_proxy.run_submit.busy_race",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                session_id=exc.session_id,
                active_run_id=exc.active_run_id,
            )
            await self.reply_rpc(
                message,
                {
                    "error": str(exc),
                    "status": "busy",
                    "session_id": exc.session_id,
                    "active_run_id": exc.active_run_id,
                },
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        except Exception as exc:
            log_error(
                "user_proxy.run_submit.failed",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                session_id=session_id,
                error=str(exc),
            )
            await self.reply_rpc(
                message,
                {"error": str(exc), "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return

        log_info(
            "user_proxy.run_submit.accepted",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            session_id=response.session_id,
            run_id=response.run_id,
            assistant_agent_id=response.assistant_agent_id,
        )
        await self.reply_rpc(message, response.model_dump(), ok=True)

    async def _handle_run_stop(self, message: AgentMessage) -> None:
        payload = dict(message.payload or {})
        run_id = str(payload.get("run_id") or message.run_id or "").strip()
        log_info(
            "user_proxy.run_stop.received",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            run_id=run_id,
            request_id=message.id,
        )
        if not run_id:
            log_warning(
                "user_proxy.run_stop.rejected",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                reason="missing_run_id",
            )
            await self.reply_rpc(
                message,
                {"error": "run_id is required", "status": "rejected"},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        response = await self.run_manager.stop_run(run_id)
        if response is None:
            log_warning(
                "user_proxy.run_stop.not_found",
                category=LOG_CATEGORY_FRONTEND_BACKEND,
                run_id=run_id,
            )
            await self.reply_rpc(
                message,
                {"error": f"run not found: {run_id}", "status": "not_found", "run_id": run_id},
                ok=False,
                level="error",
                visibility="internal",
            )
            return
        await self.update_status(
            "idle",
            reason="run.stop",
            metadata={"run_id": run_id, "stop_status": response.status},
        )
        log_info(
            "user_proxy.run_stop.accepted",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            run_id=run_id,
            status=response.status,
        )
        await self.reply_rpc(message, response.model_dump(), ok=True)

    async def _start_run(self, request: CreateRunRequest) -> CreateRunAcceptedResponse:
        session_id = str(request.session_id or "").strip()
        if not session_id:
            raise RuntimeError("session_id is required")

        assistant_record = await self.roster_manager.ensure_primary_entry_assistant(session_id)
        requested_strategy = self._resolve_requested_strategy(request)
        active_run = await self.run_manager.open_run(
            session_id,
            self.agent_id,
            entry_assistant_id=assistant_record.id,
            metadata={
                "user_agent_id": self.agent_id,
                "assistant_agent_id": assistant_record.id,
                "strategy": requested_strategy,
                "requested_strategy": requested_strategy,
            },
        )
        controller_message = AgentMessage.build_event(
            topic="run.controller.input",
            sender_id="external:http",
            target_id=self.agent_id,
            payload={
                "content": request.content,
                "session_id": session_id,
                "strategy": "react",
                "requested_strategy": requested_strategy,
                "history": deepcopy(request.history),
                "llm_config": deepcopy(request.llm_config) if request.llm_config else None,
                "system_prompt": request.system_prompt,
                "work_path": request.work_path,
                "request_overrides": deepcopy(request.request_overrides),
                "assistant_agent_id": assistant_record.id,
                "run_request": request.model_dump(),
            },
            run_id=active_run.run_id,
            session_id=session_id,
            visibility="internal",
            level="info",
            metadata={
                "assistant_agent_id": assistant_record.id,
                "requested_strategy": requested_strategy,
            },
        )
        self.center.dispatch_background(controller_message)
        log_info(
            "user_proxy.run_started",
            session_id=session_id,
            run_id=active_run.run_id,
            assistant_agent_id=assistant_record.id,
            strategy=requested_strategy,
        )

        return CreateRunAcceptedResponse(
            run_id=active_run.run_id,
            session_id=session_id,
            user_agent_id=self.agent_id,
            assistant_agent_id=assistant_record.id,
        )

    async def _handle_controller_message(self, message: AgentMessage) -> None:
        if not get_session_id(message):
            return
        if self.task_manager is None:
            raise RuntimeError("TaskManager is required for user proxy execution handling")
        result = await self.task_manager.host_message_task(
            message,
            agent=self,
            execution_engine=self.execution_engine,
        )
        if result.protocol_violation is not None:
            await self._handle_protocol_violation(message, result)
            return
        if not result.ok:
            await self._handle_execution_failure(message, result)
            return
        if result.directive is None:
            return
        await self.directive_runner.execute(message, result.directive)

    async def validate_execution_directive(
        self,
        message: AgentMessage,
        directive,
        execution_context: Optional[ExecutionContext] = None,
    ) -> None:
        if directive.kind != "handoff":
            return
        instruction = str(directive.args.get("instruction") or "").strip()
        if not instruction:
            raise RuntimeError("handoff requires instruction")
        await resolve_handoff_target(
            self.roster_manager.agent_profile_repository,
            target_profile=str(directive.args.get("target_profile") or ""),
            current_profile_id=None,
        )

    def _resolve_requested_strategy(self, request: CreateRunRequest) -> str:
        if request.strategy:
            return str(request.strategy).strip().lower() or "react"
        request_overrides = request.request_overrides or {}
        if isinstance(request_overrides, dict) and request_overrides.get("strategy"):
            return str(request_overrides.get("strategy")).strip().lower() or "react"
        return "react"

    async def _handle_protocol_violation(self, message: AgentMessage, result) -> None:
        attempted_directive_kind = self._attempted_directive_kind(message, result)
        result_summary = self._result_summary(result)
        payload = {
            "offending_agent_id": self.agent_id,
            "source_message_id": message.id,
            "source_message_type": message.message_type,
            "source_rpc_phase": message.rpc_phase,
            "source_topic": message.topic,
            "reason": str(result.protocol_violation or "user proxy protocol error"),
            "attempted_directive_kind": attempted_directive_kind,
            "result_summary": result_summary,
        }
        await self.observe(
            "user_proxy.protocol_error",
            payload=payload,
            run_id=message.run_id,
            message_id=message.id,
            visibility="internal",
            level="error",
            metadata={"session_id": message.session_id},
        )
        if not message.run_id:
            return
        await self.run_manager.fail_run(
            message.run_id,
            str(payload["reason"]),
            message_id=message.id,
            result_payload={
                "attempted_directive_kind": attempted_directive_kind,
                "result_summary": result_summary,
                "status": "error",
            },
        )

    async def _handle_execution_failure(self, message: AgentMessage, result) -> None:
        error_text = str(
            result.payload.get("error")
            or result.payload.get("reply")
            or "user proxy execution failed"
        )
        await self.observe(
            "agent.error",
            payload={
                "offending_agent_id": self.agent_id,
                "source_message_id": message.id,
                "source_message_type": message.message_type,
                "source_rpc_phase": message.rpc_phase,
                "source_topic": message.topic,
                "reason": error_text,
            },
            run_id=message.run_id,
            message_id=message.id,
            visibility="internal",
            level="error",
            metadata={"session_id": message.session_id},
        )
        if message.run_id:
            await self.run_manager.fail_run(
                message.run_id,
                error_text,
                message_id=message.id,
                result_payload=dict(result.payload),
            )

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


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.roster_manager import AgentRosterManager
    from run_manager import RunManager
