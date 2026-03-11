from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from agents.execution.message_utils import (
    get_llm_config,
    get_strategy_name,
    render_current_message,
)
from agents.execution.react_strategy import ReactStrategy
from agents.execution.simple_strategy import SimpleStrategy
from agents.execution.step_emitter import StepEmitter
from agents.execution.strategy import AgentStrategy, ExecutionStep
from agents.execution.tool_executor import ToolExecutor
from models import LLMConfig

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
        memory: Optional[Any] = None,
        llm_client_factory: Optional[Callable[["AgentMessage"], Any]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        step_emitter: Optional[StepEmitter] = None,
        strategies: Optional[Dict[str, AgentStrategy]] = None,
    ) -> None:
        self.agent = agent
        self.memory = memory
        self.llm_client_factory = llm_client_factory
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
        agent_id = self.agent.agent_id
        strategy = self._resolve_strategy(message)

        reply = ""
        ok = True
        terminal_status = "completed"
        final_step: Optional[ExecutionStep] = None
        try:
            llm_client = self._build_llm_client(message)

            async for step in strategy.execute(
                message,
                agent_id=agent_id,
                llm_client=llm_client,
                tool_executor=self.tool_executor,
                memory=self.memory,
            ):
                await self.step_emitter.emit_step(message, agent_id=agent_id, step=step)
                final_step = step
                if step.step_type == "answer":
                    reply = step.content
                elif step.step_type == "error":
                    ok = False
                    terminal_status = str(step.metadata.get("status") or "error")
                    reply = step.content

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
            final_step = ExecutionStep("error", reply, {"status": "error"})
            await self.step_emitter.emit_step(message, agent_id=agent_id, step=final_step)

        payload: Dict[str, object] = {
            "reply": reply,
            "handled_by": agent_id,
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

    def _build_llm_client(self, message: "AgentMessage"):
        if self.llm_client_factory is not None:
            return self.llm_client_factory(message)
        llm_config = get_llm_config(message)
        if not llm_config:
            return None
        from llm.client import create_llm_client

        config = LLMConfig.model_validate(llm_config)
        return create_llm_client(config)

    def _resolve_strategy(self, message: "AgentMessage") -> AgentStrategy:
        strategy = self._strategies.get(get_strategy_name(message))
        if strategy is not None:
            return strategy
        return self._strategies["simple"]
