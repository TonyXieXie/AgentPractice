from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, TYPE_CHECKING

from agents.execution.context_builder import ContextBuilder
from agents.execution.react_strategy import ReactStrategy
from agents.execution.simple_strategy import SimpleStrategy
from agents.execution.step_emitter import StepEmitter
from agents.execution.strategy import AgentStrategy, ExecutionRequest, ExecutionStep
from agents.execution.tool_executor import ToolExecutor

if TYPE_CHECKING:
    from agents.base import AgentBase
    from agents.message import AgentMessage


@dataclass(slots=True)
class ExecutionResult:
    ok: bool
    payload: Dict[str, object]
    final_step: Optional[ExecutionStep] = None


class ExecutionEngine:
    def __init__(
        self,
        agent: "AgentBase",
        *,
        context_builder: Optional[ContextBuilder] = None,
        tool_executor: Optional[ToolExecutor] = None,
        step_emitter: Optional[StepEmitter] = None,
        strategies: Optional[Dict[str, AgentStrategy]] = None,
    ) -> None:
        self.agent = agent
        self.context_builder = context_builder or ContextBuilder()
        self.tool_executor = tool_executor or ToolExecutor()
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
        request = self.context_builder.build_request(
            message,
            agent_id=self.agent.agent_id,
            default_strategy="simple",
        )
        strategy = self._resolve_strategy(request)

        reply = ""
        ok = True
        terminal_status = "completed"
        final_step: Optional[ExecutionStep] = None
        run_started = False
        try:
            llm_client = self.context_builder.build_llm_client(request)
            await self.step_emitter.emit_run_started(request, strategy_name=strategy.name)
            run_started = True

            async for step in strategy.execute(
                request,
                llm_client=llm_client,
                tool_executor=self.tool_executor,
                context_builder=self.context_builder,
            ):
                await self.step_emitter.emit_step(request, step)
                final_step = step
                if step.step_type == "answer":
                    reply = step.content
                elif step.step_type == "error":
                    ok = False
                    terminal_status = str(step.metadata.get("status") or "error")
                    reply = step.content

            if not reply and ok:
                reply = request.user_input
        except asyncio.CancelledError:
            ok = False
            terminal_status = "stopped"
            reply = "run cancelled"
            final_step = ExecutionStep("error", reply, {"status": "stopped"})
            if run_started:
                await self.step_emitter.emit_step(request, final_step)
            raise
        except Exception as exc:
            ok = False
            terminal_status = "error"
            reply = str(exc)
            final_step = ExecutionStep("error", reply, {"status": "error"})
            if run_started:
                await self.step_emitter.emit_step(request, final_step)
        finally:
            if run_started:
                await self.step_emitter.emit_run_finished(
                    request,
                    strategy_name=strategy.name,
                    status=terminal_status if not ok else "completed",
                    reply=reply,
                )

        payload: Dict[str, object] = {
            "reply": reply,
            "handled_by": self.agent.agent_id,
            "strategy": strategy.name,
            "status": terminal_status if not ok else "completed",
        }
        if final_step and final_step.metadata.get("tool_call_id"):
            payload["tool_call_id"] = final_step.metadata.get("tool_call_id")
        if final_step and final_step.metadata.get("tool_name"):
            payload["tool_name"] = final_step.metadata.get("tool_name")
        if not ok:
            payload["error"] = reply
        return ExecutionResult(ok=ok, payload=payload, final_step=final_step)

    def _resolve_strategy(self, request: ExecutionRequest) -> AgentStrategy:
        strategy = self._strategies.get(request.strategy_name)
        if strategy is not None:
            return strategy
        return self._strategies["simple"]
