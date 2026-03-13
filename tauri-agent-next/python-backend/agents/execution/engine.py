from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from agents.execution.directives import (
    ExecutionDirective,
    allowed_directive_kinds_for_agent,
    visible_directive_kinds_for_agent,
)
from agents.execution.message_utils import (
    get_llm_config,
    get_strategy_name,
    get_tool_name,
    render_current_message,
)
from llm.default_config import get_default_llm_config
from agents.execution.react_strategy import ReactStrategy
from agents.execution.simple_strategy import SimpleStrategy
from agents.execution.step_emitter import StepEmitter
from agents.execution.strategy import AgentStrategy, ExecutionContext, ExecutionStep
from agents.execution.tool_executor import ToolExecutor
from models import LLMConfig

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage


@dataclass(slots=True)
class ExecutionResult:
    ok: bool
    payload: Dict[str, object]
    directive: Optional[ExecutionDirective] = None
    final_step: Optional[ExecutionStep] = None
    agent_status: Optional[str] = None
    protocol_violation: Optional[str] = None


class ExecutionEngine:
    def __init__(
        self,
        agent: "AgentBase",
        *,
        memory: Optional[Any] = None,
        llm_client_factory: Optional[Callable[["AgentMessage"], Any]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        step_emitter: Optional[StepEmitter] = None,
        strategies: Optional[Dict[str, AgentStrategy]] = None,
    ) -> None:
        self.agent = agent
        self.memory = memory
        self.llm_client_factory = llm_client_factory
        self.allowed_directive_kinds = allowed_directive_kinds_for_agent(
            agent_type=getattr(agent.instance, "agent_type", None),
            role=getattr(agent.instance, "role", None),
        )
        self.visible_directive_kinds = visible_directive_kinds_for_agent(
            agent_type=getattr(agent.instance, "agent_type", None),
            role=getattr(agent.instance, "role", None),
        )
        base_tool_executor = tool_executor or ToolExecutor()
        self.tool_executor = base_tool_executor.clone(
            allowed_builtin_tool_names=self.visible_directive_kinds,
        )
        self.step_emitter = step_emitter or StepEmitter(agent)
        self._strategies: Dict[str, AgentStrategy] = {
            "simple": SimpleStrategy(),
            "react": ReactStrategy(),
        }
        if strategies:
            self._strategies.update(strategies)

    def register_strategy(self, strategy: AgentStrategy) -> None:
        self._strategies[strategy.name] = strategy

    async def execute(self, message: "AgentMessage") -> ExecutionResult:
        agent_id = self.agent.agent_id
        strategy = self._resolve_strategy(message)
        execution_context: Optional[ExecutionContext] = None

        reply = ""
        ok = True
        terminal_status = "completed"
        directive: Optional[ExecutionDirective] = None
        final_step: Optional[ExecutionStep] = None
        protocol_violation: Optional[str] = None
        try:
            llm_client = self._build_llm_client(message)
            execution_context = await self._build_execution_context()

            async for step in strategy.execute(
                message,
                agent_id=agent_id,
                llm_client=llm_client,
                tool_executor=self.tool_executor,
                memory=self.memory,
                execution_context=execution_context,
            ):
                await self.step_emitter.emit_step(message, agent_id=agent_id, step=step)
                final_step = step
                if step.step_type == "answer":
                    reply = step.content
                elif step.step_type == "directive":
                    directive_payload = step.metadata.get("directive")
                    if isinstance(directive_payload, dict):
                        directive = ExecutionDirective.from_dict(directive_payload)
                    elif isinstance(step.content, str) and step.content.strip():
                        directive = ExecutionDirective(kind=step.content.strip())
                    break
                elif step.step_type == "error":
                    ok = False
                    terminal_status = str(step.metadata.get("status") or "error")
                    reply = step.content

            if directive is not None:
                await self._validate_directive(
                    message,
                    directive,
                    execution_context=execution_context,
                )

            if not reply and ok:
                reply = render_current_message(message)
        except asyncio.CancelledError:
            ok = False
            terminal_status = "stopped"
            reply = "run cancelled"
            final_step = ExecutionStep("error", reply, {"status": "stopped"})
            await self.step_emitter.emit_step(message, agent_id=agent_id, step=final_step)
            raise
        except Exception as exc:
            ok = False
            terminal_status = "error"
            reply = str(exc)
            directive = None
            final_step = ExecutionStep("error", reply, {"status": "error"})
            await self.step_emitter.emit_step(message, agent_id=agent_id, step=final_step)

        if terminal_status != "stopped" and ok:
            protocol_violation = self._protocol_violation_reason(message, directive)
            if protocol_violation is not None:
                ok = False
                terminal_status = "error"
                if not reply:
                    reply = protocol_violation

        payload: Dict[str, object] = {
            "reply": reply,
            "handled_by": agent_id,
            "strategy": strategy.name,
            "status": terminal_status if not ok else "completed",
        }
        agent_status = "idle" if ok else "error"
        if directive is not None and ok and protocol_violation is None:
            payload = self._payload_for_directive(directive, agent_id=agent_id, strategy_name=strategy.name)
            agent_status = self._agent_status_for_directive(directive)
        elif protocol_violation is not None:
            payload["status"] = "error"
            payload["error"] = protocol_violation
            payload["protocol_violation"] = protocol_violation
            if directive is not None:
                payload["attempted_directive"] = directive.to_dict()
            agent_status = self._agent_status_for_protocol_violation()
        if final_step and final_step.metadata.get("tool_call_id"):
            payload["tool_call_id"] = final_step.metadata.get("tool_call_id")
        if final_step and final_step.metadata.get("tool_name"):
            payload["tool_name"] = final_step.metadata.get("tool_name")
        if not ok and "error" not in payload:
            payload["error"] = reply
        return ExecutionResult(
            ok=ok,
            payload=payload,
            directive=directive,
            final_step=final_step,
            agent_status=agent_status,
            protocol_violation=protocol_violation,
        )

    async def _validate_directive(
        self,
        message: "AgentMessage",
        directive: ExecutionDirective,
        *,
        execution_context: Optional[ExecutionContext],
    ) -> None:
        validator = getattr(self.agent, "validate_execution_directive", None)
        if not callable(validator):
            return
        result = validator(
            message,
            directive,
            execution_context=execution_context,
        )
        if asyncio.iscoroutine(result):
            await result

    def _build_llm_client(self, message: "AgentMessage"):
        llm_config = get_llm_config(message)
        if llm_config is None:
            llm_config = get_default_llm_config()
        if self.llm_client_factory is not None:
            return self.llm_client_factory(message)
        if not llm_config:
            return None
        from llm.client import create_llm_client

        config = LLMConfig.model_validate(llm_config)
        return create_llm_client(config)

    async def _build_execution_context(self) -> Optional[ExecutionContext]:
        builder = getattr(self.agent, "build_execution_context", None)
        if callable(builder):
            context = builder(self.tool_executor)
            if asyncio.iscoroutine(context):
                context = await context
            if context is not None:
                return context
        return ExecutionContext(tool_executor=self.tool_executor)

    def _resolve_strategy(self, message: "AgentMessage") -> AgentStrategy:
        strategy = self._strategies.get(get_strategy_name(message, default="react"))
        if strategy is not None:
            return strategy
        for fallback_name in ("react", "simple"):
            fallback = self._strategies.get(fallback_name)
            if fallback is not None:
                return fallback
        return next(iter(self._strategies.values()))

    def _payload_for_directive(
        self,
        directive: ExecutionDirective,
        *,
        agent_id: str,
        strategy_name: str,
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "handled_by": agent_id,
            "strategy": strategy_name,
            "directive": directive.to_dict(),
            "status": "completed",
        }
        if directive.kind == "finish_run":
            payload["status"] = str(directive.args.get("status") or "completed")
            payload["reply"] = str(directive.args.get("reply") or "")
        elif directive.kind == "fail_run":
            payload["status"] = "error"
            payload["error"] = str(directive.args.get("error") or "run failed")
            payload["reply"] = str(directive.args.get("error") or "run failed")
        elif directive.kind == "stop_run":
            payload["status"] = "stopped"
        return payload

    def _agent_status_for_directive(self, directive: ExecutionDirective) -> str:
        if directive.kind == "fail_run":
            return "error"
        return "idle"

    def _protocol_violation_reason(
        self,
        message: "AgentMessage",
        directive: Optional[ExecutionDirective],
    ) -> Optional[str]:
        if directive is None:
            return (
                f"{self.agent.instance.role or self.agent.instance.agent_type} execution must emit "
                "exactly one allowed control directive"
            )
        requested_tool_name = get_tool_name(message)
        allowed_kinds = (
            self.allowed_directive_kinds
            if requested_tool_name in self.allowed_directive_kinds
            else self.visible_directive_kinds
        )
        if directive.kind not in allowed_kinds:
            return (
                f"directive not allowed for "
                f"{self.agent.instance.role or self.agent.instance.agent_type}: {directive.kind}"
            )
        return None

    def _agent_status_for_protocol_violation(self) -> str:
        agent_type = str(self.agent.instance.agent_type or "").strip().lower()
        role = str(self.agent.instance.role or "").strip().lower()
        if agent_type == "user_proxy" or role == "user_proxy":
            return "error"
        return "idle"
