"""
ReActAgent - Reasoning + Acting Agent

Implements a ReAct-style loop with tool calling.
- OpenAI: uses native tool calling (tool_calls / function_call)
- Other providers: uses text-based Action/Action Input parsing
"""

import json
import re
import traceback
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple

import httpx
from .base import AgentStrategy, AgentStep
from tools.base import Tool, tool_to_openai_function, tool_to_openai_responses_tool


class ReActAgent(AgentStrategy):
    """
    ReAct (Reasoning + Acting) Agent.

    Iteratively:
    - Thinks about the next step
    - Takes an action (uses a tool)
    - Observes the result
    - Continues until reaching a final answer
    """

    def __init__(self, max_iterations: int = 5, system_prompt: Optional[str] = None):
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or ""
    
    def _merge_debug_context(
        self,
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]],
        agent_type: str,
        iteration: int
    ) -> Optional[Dict[str, Any]]:
        debug_ctx: Dict[str, Any] = {}
        if request_overrides and isinstance(request_overrides.get("_debug"), dict):
            debug_ctx.update(request_overrides.get("_debug", {}))
        if session_id:
            debug_ctx["session_id"] = session_id
        if "message_id" not in debug_ctx:
            debug_ctx["message_id"] = None
        debug_ctx["agent_type"] = agent_type
        debug_ctx["iteration"] = iteration
        return debug_ctx if debug_ctx else None

    async def execute(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AgentStep, None]:
        profile = getattr(llm_client.config, "api_profile", None) or getattr(llm_client.config, "api_type", None)
        profile = (profile or "openai").lower()
        if profile in ("openai", "openai_compatible", "deepseek"):
            async for step in self._execute_openai_tool_calling(
                user_input=user_input,
                history=history,
                tools=tools,
                llm_client=llm_client,
                session_id=session_id,
                request_overrides=request_overrides
            ):
                yield step
            return

        async for step in self._execute_text_react(
            user_input=user_input,
            history=history,
            tools=tools,
            llm_client=llm_client,
            session_id=session_id,
            request_overrides=request_overrides
        ):
            yield step

    async def _execute_openai_tool_calling(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]]
    ) -> AsyncGenerator[AgentStep, None]:
        prompt = self.build_prompt(user_input, history, tools, {"tool_calling": True})
        profile = getattr(llm_client.config, "api_profile", None) or getattr(llm_client.config, "api_type", None)
        profile = (profile or "openai").lower()
        prompt_role = "developer" if profile == "openai" else "system"

        messages: List[Dict[str, Any]] = [{"role": prompt_role, "content": prompt}]
        if history:
            messages.extend(history)
        user_content = None
        if request_overrides and request_overrides.get("user_content") is not None:
            user_content = request_overrides.get("user_content")
        messages.append({"role": "user", "content": user_content if user_content is not None else user_input})

        openai_format = "openai_chat_completions"
        if hasattr(llm_client, "_get_format"):
            openai_format = llm_client._get_format()

        if openai_format == "openai_responses":
            openai_tools = [tool_to_openai_responses_tool(t) for t in tools] if tools else []
        else:
            openai_tools = [tool_to_openai_function(t) for t in tools] if tools else []

        response_input = self._build_responses_input(messages)
        max_no_answer_attempts = 3

        for iteration in range(self.max_iterations):
            no_answer_attempts = 0
            while True:
                llm_overrides = dict(request_overrides) if request_overrides else {}
                if openai_tools:
                    llm_overrides.setdefault("tools", openai_tools)
                    if openai_format != "openai_responses":
                        llm_overrides.setdefault("tool_choice", "auto")

                debug_ctx = self._merge_debug_context(session_id, request_overrides, "react", iteration)
                if debug_ctx:
                    llm_overrides["_debug"] = debug_ctx

                if openai_format == "openai_responses":
                    llm_overrides["input"] = response_input

                    max_connect_retries = 3
                    connect_attempt = 0
                    connect_ok = False
                    content_buffer = ""
                    reasoning_buffer = ""
                    tool_calls = []
                    response_output_items: List[Dict[str, Any]] = []
                    thought_stream_key = f"assistant_content_{iteration}"
                    reasoning_stream_key = f"assistant_reasoning_{iteration}"
                    stream_mode = "answer"
                    stopped = False

                    while connect_attempt < max_connect_retries:
                        connect_attempt += 1
                        content_buffer = ""
                        reasoning_buffer = ""
                        tool_calls = []
                        response_output_items = []
                        stream_mode = "answer"
                        stopped = False
                        received_any = False

                        if connect_attempt > 1:
                            yield AgentStep(
                                step_type="thought",
                                content=f"网络连接中（第{connect_attempt}/{max_connect_retries}次）...",
                                metadata={"iteration": iteration, "stream_key": thought_stream_key, "network_retry": connect_attempt}
                            )

                        try:
                            async for event in llm_client.chat_stream_events(messages, llm_overrides if llm_overrides else None):
                                received_any = True
                                event_type = event.get("type")
                                if event_type == "content":
                                    delta = event.get("delta", "")
                                    if delta:
                                        content_buffer += delta
                                        step_type = "answer_delta" if stream_mode == "answer" else "thought_delta"
                                        yield AgentStep(
                                            step_type=step_type,
                                            content=delta,
                                            metadata={"iteration": iteration, "stream_key": thought_stream_key}
                                        )
                                elif event_type == "reasoning":
                                    delta = event.get("delta", "")
                                    if delta:
                                        reasoning_buffer += delta
                                        yield AgentStep(
                                            step_type="thought_delta",
                                            content=delta,
                                            metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                                        )
                                elif event_type == "tool_call_delta":
                                    if stream_mode != "thought":
                                        stream_mode = "thought"
                                    call_index = event.get("index", 0)
                                    call_key = f"tool-{iteration}-{call_index}"
                                    tool_name = event.get("name") or ""
                                    args_delta = event.get("arguments_delta", "")
                                    if args_delta or tool_name:
                                        yield AgentStep(
                                            step_type="action_delta",
                                            content=args_delta,
                                            metadata={
                                                "iteration": iteration,
                                                "stream_key": call_key,
                                                "tool": tool_name,
                                                "call_index": call_index
                                            }
                                        )
                                elif event_type == "done":
                                    content_buffer = event.get("content", "") or ""
                                    tool_calls = event.get("tool_calls", []) or []
                                    response_obj = event.get("response") or {}
                                    if isinstance(response_obj, dict):
                                        response_output_items = response_obj.get("output", []) or []
                                    stopped = bool(event.get("stopped"))
                            connect_ok = True
                            break
                        except httpx.ConnectError as e:
                            if received_any:
                                yield AgentStep(
                                    step_type="error",
                                    content="网络错误：连接中断，请重试。",
                                    metadata={"error": str(e), "error_type": "ConnectError"}
                                )
                                return
                            if connect_attempt >= max_connect_retries:
                                yield AgentStep(
                                    step_type="error",
                                    content=f"网络错误：连接失败（已重试{max_connect_retries}次）",
                                    metadata={"error": str(e), "error_type": "ConnectError"}
                                )
                                return
                            continue

                    if not connect_ok:
                        return

                    llm_call_id = None
                    if llm_overrides.get("_debug"):
                        llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")

                    if stopped:
                        stopped_text = self._append_stop_note(content_buffer)
                        if llm_call_id:
                            self._update_llm_processed(llm_call_id, {
                                "stopped_by_user": True,
                                "content": stopped_text
                            })
                        yield AgentStep(
                            step_type="answer",
                            content=stopped_text,
                            metadata={"agent_type": "react", "iterations": iteration + 1, "stopped_by_user": True}
                        )
                        return

                    prepared_calls: List[Dict[str, Any]] = []
                    if tool_calls:
                        sanitized_tool_calls: List[Dict[str, Any]] = []
                        for call_index, call in enumerate(tool_calls):
                            call_index = call.get("index", call_index)
                            tool_name = call.get("name")
                            call_id = call.get("call_id") or call.get("id") or f"call_{iteration}_{call_index}"
                            args_text = call.get("arguments", "")
                            _, parse_error = self._safe_json_loads(args_text)
                            sanitized_args = "{}" if parse_error else args_text
                            sanitized_call = dict(call)
                            sanitized_call["arguments"] = sanitized_args
                            sanitized_tool_calls.append(sanitized_call)
                            tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
                            prepared_calls.append({
                                "call_index": call_index,
                                "tool_name": tool_name,
                                "call_id": call_id,
                                "call_key": f"tool-{iteration}-{call_index}",
                                "tool": tool,
                                "tool_input": tool_input,
                                "error_msg": error_msg
                            })
                        tool_calls = sanitized_tool_calls

                    output_items = response_output_items
                    if not output_items:
                        synthetic_items: List[Dict[str, Any]] = []
                        if content_buffer.strip():
                            synthetic_items.append({
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": content_buffer}]
                            })
                        for idx, call in enumerate(tool_calls):
                            call_id = call.get("call_id") or call.get("id") or f"call_{iteration}_{idx}"
                            call["call_id"] = call_id
                            synthetic_items.append({
                                "type": "function_call",
                                "call_id": call_id,
                                "name": call.get("name", ""),
                                "arguments": call.get("arguments", "")
                            })
                        output_items = synthetic_items

                    if tool_calls:
                        if output_items:
                            response_input += output_items
                        if llm_call_id:
                            self._update_llm_processed(llm_call_id, {
                                "tool_calls": tool_calls,
                                "content": content_buffer
                            })
                        if reasoning_buffer.strip():
                            yield AgentStep(
                                step_type="thought",
                                content=reasoning_buffer,
                                metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                            )
                        if content_buffer.strip():
                            yield AgentStep(
                                step_type="thought",
                                content=content_buffer,
                                metadata={"iteration": iteration, "stream_key": thought_stream_key}
                            )

                        for prepared in prepared_calls:
                            call_index = prepared["call_index"]
                            tool_name = prepared["tool_name"]
                            call_id = prepared["call_id"]
                            call_key = prepared["call_key"]
                            tool = prepared["tool"]
                            tool_input = prepared["tool_input"]
                            error_msg = prepared["error_msg"]
                            yield AgentStep(
                                step_type="action",
                                content=f"{tool_name}[{tool_input}]",
                                metadata={"tool": tool_name, "input": tool_input, "iteration": iteration, "stream_key": call_key}
                            )
                            tool_output = error_msg if error_msg else await self._execute_tool(tool, tool_input) if tool else f"Tool not found: '{tool_name}'"
                            yield AgentStep(
                                step_type="observation",
                                content=tool_output,
                                metadata={"tool": tool_name, "iteration": iteration}
                            )

                            response_input.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": tool_output
                            })

                        break

                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {"final_answer": content_buffer})

                    if content_buffer.strip():
                        yield AgentStep(
                            step_type="answer",
                            content=content_buffer,
                            metadata={"agent_type": "react", "iterations": iteration + 1, "stream_key": thought_stream_key}
                        )
                        return

                    no_answer_attempts += 1
                    if no_answer_attempts >= max_no_answer_attempts:
                        yield AgentStep(
                            step_type="error",
                            content="LLM returned no content.",
                            metadata={"iteration": iteration}
                        )
                        return
                    continue

                max_connect_retries = 3
                connect_attempt = 0
                connect_ok = False
                thought_stream_key = f"assistant_content_{iteration}"
                reasoning_stream_key = f"assistant_reasoning_{iteration}"

                while connect_attempt < max_connect_retries:
                    connect_attempt += 1
                    content_buffer = ""
                    reasoning_buffer = ""
                    tool_calls = []
                    stream_mode = "answer"
                    stopped = False
                    received_any = False

                    if connect_attempt > 1:
                        yield AgentStep(
                            step_type="thought",
                            content=f"网络连接中（第{connect_attempt}/{max_connect_retries}次）...",
                            metadata={"iteration": iteration, "stream_key": thought_stream_key, "network_retry": connect_attempt}
                        )

                    try:
                        async for event in llm_client.chat_stream_events(messages, llm_overrides if llm_overrides else None):
                            received_any = True
                            event_type = event.get("type")
                            if event_type == "content":
                                delta = event.get("delta", "")
                                if delta:
                                    content_buffer += delta
                                    step_type = "answer_delta" if stream_mode == "answer" else "thought_delta"
                                    yield AgentStep(
                                        step_type=step_type,
                                        content=delta,
                                        metadata={"iteration": iteration, "stream_key": thought_stream_key}
                                    )
                            elif event_type == "reasoning":
                                delta = event.get("delta", "")
                                if delta:
                                    reasoning_buffer += delta
                                    yield AgentStep(
                                        step_type="thought_delta",
                                        content=delta,
                                        metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                                    )
                            elif event_type == "tool_call_delta":
                                if stream_mode != "thought":
                                    stream_mode = "thought"
                                call_index = event.get("index", 0)
                                call_key = f"tool-{iteration}-{call_index}"
                                tool_name = event.get("name") or ""
                                args_delta = event.get("arguments_delta", "")
                                if args_delta or tool_name:
                                    yield AgentStep(
                                        step_type="action_delta",
                                        content=args_delta,
                                        metadata={
                                            "iteration": iteration,
                                            "stream_key": call_key,
                                            "tool": tool_name,
                                            "call_index": call_index
                                        }
                                    )
                            elif event_type == "done":
                                content_buffer = event.get("content", "") or ""
                                tool_calls = event.get("tool_calls", []) or []
                                stopped = bool(event.get("stopped"))
                        connect_ok = True
                        break
                    except httpx.ConnectError as e:
                        if received_any:
                            yield AgentStep(
                                step_type="error",
                                content="网络错误：连接中断，请重试。",
                                metadata={"error": str(e), "error_type": "ConnectError"}
                            )
                            return
                        if connect_attempt >= max_connect_retries:
                            yield AgentStep(
                                step_type="error",
                                content=f"网络错误：连接失败（已重试{max_connect_retries}次）",
                                metadata={"error": str(e), "error_type": "ConnectError"}
                            )
                            return
                        continue

                if not connect_ok:
                    return

                llm_call_id = None
                if llm_overrides.get("_debug"):
                    llm_call_id = llm_overrides.get("_debug", {}).get("llm_call_id")

                if stopped:
                    stopped_text = self._append_stop_note(content_buffer)
                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {
                            "stopped_by_user": True,
                            "content": stopped_text
                        })
                    yield AgentStep(
                        step_type="answer",
                        content=stopped_text,
                        metadata={"agent_type": "react", "iterations": iteration + 1, "stopped_by_user": True}
                    )
                    return

                if tool_calls:

                    if reasoning_buffer.strip():
                        yield AgentStep(
                            step_type="thought",
                            content=reasoning_buffer,
                            metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                        )

                    if content_buffer.strip():
                        yield AgentStep(
                            step_type="thought",
                            content=content_buffer,
                            metadata={"iteration": iteration, "stream_key": thought_stream_key}
                        )

                    prepared_calls: List[Dict[str, Any]] = []
                    sanitized_tool_calls: List[Dict[str, Any]] = []
                    for call_index, call in enumerate(tool_calls):
                        call_index = call.get("index", call_index) if isinstance(call, dict) else call_index
                        function = call.get("function", {}) or {}
                        tool_name = function.get("name")
                        args_text = function.get("arguments", "")
                        call_id = call.get("id")
                        _, parse_error = self._safe_json_loads(args_text)
                        sanitized_args = "{}" if parse_error else args_text
                        sanitized_call = dict(call)
                        sanitized_func = dict(function)
                        sanitized_func["arguments"] = sanitized_args
                        sanitized_call["function"] = sanitized_func
                        sanitized_tool_calls.append(sanitized_call)
                        tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
                        prepared_calls.append({
                            "call_index": call_index,
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "call_key": f"tool-{iteration}-{call_index}",
                            "tool": tool,
                            "tool_input": tool_input,
                            "error_msg": error_msg
                        })

                    if llm_call_id:
                        self._update_llm_processed(llm_call_id, {
                            "tool_calls": sanitized_tool_calls,
                            "content": content_buffer
                        })

                    messages.append({
                        "role": "assistant",
                        "content": content_buffer,
                        "tool_calls": sanitized_tool_calls
                    })

                    for prepared in prepared_calls:
                        call_index = prepared["call_index"]
                        tool_name = prepared["tool_name"]
                        call_id = prepared["call_id"]
                        call_key = prepared["call_key"]
                        tool = prepared["tool"]
                        tool_input = prepared["tool_input"]
                        error_msg = prepared["error_msg"]
                        yield AgentStep(
                            step_type="action",
                            content=f"{tool_name}[{tool_input}]",
                            metadata={"tool": tool_name, "input": tool_input, "iteration": iteration, "stream_key": call_key}
                        )
                        tool_output = error_msg if error_msg else await self._execute_tool(tool, tool_input) if tool else f"Tool not found: '{tool_name}'"
                        yield AgentStep(
                            step_type="observation",
                            content=tool_output,
                            metadata={"tool": tool_name, "iteration": iteration}
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": tool_output
                        })

                    break

                if llm_call_id:
                    self._update_llm_processed(llm_call_id, {"final_answer": content_buffer})

                if reasoning_buffer.strip():
                    yield AgentStep(
                        step_type="thought",
                        content=reasoning_buffer,
                        metadata={"iteration": iteration, "stream_key": reasoning_stream_key, "reasoning": True}
                    )

                if content_buffer.strip():
                    yield AgentStep(
                        step_type="answer",
                        content=content_buffer,
                        metadata={"agent_type": "react", "iterations": iteration + 1, "stream_key": thought_stream_key}
                    )
                    return

                no_answer_attempts += 1
                if no_answer_attempts >= max_no_answer_attempts:
                    yield AgentStep(
                        step_type="error",
                        content="LLM returned no content.",
                        metadata={"iteration": iteration}
                    )
                    return

        yield AgentStep(
            step_type="answer",
            content="Sorry, I could not complete the task within the limit.",
            metadata={"agent_type": "react", "iterations": self.max_iterations, "max_iterations_reached": True}
        )

    async def _execute_text_react(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str],
        request_overrides: Optional[Dict[str, Any]]
    ) -> AsyncGenerator[AgentStep, None]:
        scratchpad: List[str] = []
        user_content = None
        if request_overrides and request_overrides.get("user_content") is not None:
            user_content = request_overrides.get("user_content")

        for iteration in range(self.max_iterations):
            prompt = self.build_prompt(user_input, history, tools, {
                "scratchpad": scratchpad,
                "iteration": iteration,
                "tool_calling": False
            })

            try:
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content if user_content is not None else user_input}
                ]

                llm_overrides = dict(request_overrides) if request_overrides else {}
                debug_ctx = self._merge_debug_context(session_id, request_overrides, "react", iteration)
                if debug_ctx:
                    llm_overrides["_debug"] = debug_ctx

                response = await llm_client.chat(messages, llm_overrides if llm_overrides else None)
                llm_output = response.get("content", "")

            except Exception as e:
                yield AgentStep(
                    step_type="error",
                    content=f"LLM call failed: {str(e)}",
                    metadata={"iteration": iteration, "error": str(e), "traceback": traceback.format_exc()}
                )
                return

            thought, action, action_input, final_answer = self._parse_reaction(llm_output)
            llm_call_id = response.get("llm_call_id")
            if llm_call_id:
                self._update_llm_processed(llm_call_id, {
                    "thought": thought,
                    "action": action,
                    "action_input": action_input,
                    "final_answer": final_answer
                })

            if final_answer:
                yield AgentStep(
                    step_type="answer",
                    content=final_answer,
                    metadata={"agent_type": "react", "iterations": iteration + 1, "scratchpad": scratchpad}
                )
                return

            if thought:
                yield AgentStep(
                    step_type="thought",
                    content=thought,
                    metadata={"iteration": iteration}
                )
                scratchpad.append(f"Thought: {thought}")

            if action and action_input:
                yield AgentStep(
                    step_type="action",
                    content=f"{action}[{action_input}]",
                    metadata={"tool": action, "input": action_input, "iteration": iteration}
                )
                scratchpad.append(f"Action: {action}")
                scratchpad.append(f"Action Input: {action_input}")

                tool = self._get_tool(tools, action)
                if tool:
                    try:
                        observation = await tool.execute(action_input)
                        yield AgentStep(
                            step_type="observation",
                            content=observation,
                            metadata={"tool": action, "iteration": iteration}
                        )
                        scratchpad.append(f"Observation: {observation}")
                    except Exception as e:
                        error_msg = f"Tool execution failed: {str(e)}"
                        yield AgentStep(
                            step_type="observation",
                            content=error_msg,
                            metadata={"tool": action, "error": str(e), "iteration": iteration}
                        )
                        scratchpad.append(f"Observation: {error_msg}")
                else:
                    error_msg = f"Tool not found: '{action}'"
                    yield AgentStep(
                        step_type="error",
                        content=error_msg,
                        metadata={"tool": action, "iteration": iteration}
                    )
                    scratchpad.append(f"Observation: {error_msg}")
            else:
                yield AgentStep(
                    step_type="thought",
                    content="(Agent could not determine next action)",
                    metadata={"iteration": iteration, "warning": "no_action"}
                )

        yield AgentStep(
            step_type="answer",
            content="Sorry, I could not complete the task within the limit.",
            metadata={"agent_type": "react", "iterations": self.max_iterations, "max_iterations_reached": True}
        )

    def build_prompt(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        tool_names = ", ".join([tool.name for tool in tools]) if tools else "(no tools available)"
        tool_calling = bool(additional_context and additional_context.get("tool_calling"))
        scratchpad = additional_context.get("scratchpad", []) if additional_context else []
        scratchpad_text = "\n".join(scratchpad) if scratchpad else "(first iteration)"

        base_prompt = (self.system_prompt or "").strip()
        sections: List[str] = []
        if base_prompt:
            sections.append(base_prompt)

        if tool_calling:
            sections.append(
                "You are a reasoning + acting assistant. Use tools via function/tool calling when needed.\n\n"
                "## Tools\n"
                f"Available tool names: {tool_names}\n"
                "Tool definitions are provided separately via the API tools field.\n\n"
                "Guidelines:\n"
                "- If a tool is needed, call it with JSON arguments that match its schema.\n"
                "- Use code_ast to get a structural outline of files before deep edits.\n"
                "- Prefer rg for searching file contents.\n"
                "- Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\n"
                "- apply_patch format (strict):\n"
                "  *** Begin Patch\n"
                "  *** Update File: path\n"
                "  @@\n"
                "  - old line\n"
                "  + new line\n"
                "  *** End Patch\n"
                "- Each change line must start with + or -, and context lines must be included under @@ hunks.\n"
                "- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n"
                "- apply_patch matches by context; if the match is not unique, request more surrounding context.\n"
                "- If apply_patch fails due to context, ask for more context and retry.\n"
                "- If no tool is needed, answer directly."
            )
            return "\n\n".join(sections).strip()

        sections.append(
            "You are a reasoning + acting assistant. Follow the format exactly.\n\n"
            "## Tools\n"
            f"Available tool names: {tool_names}\n"
            "Tool definitions are provided separately via the API tools field.\n"
            "Guidelines:\n"
            "- Use code_ast to get a structural outline of files before deep edits.\n"
            "- Prefer rg for searching file contents.\n"
            "- Prefer apply_patch for file modifications; avoid rewriting entire files unless necessary.\n"
            "- apply_patch format (strict):\n"
            "  *** Begin Patch\n"
            "  *** Update File: path\n"
            "  @@\n"
            "  - old line\n"
            "  + new line\n"
            "  *** End Patch\n"
            "- Do NOT wrap apply_patch content in code fences; send raw patch text only.\n"
            "- If apply_patch context is not unique, request more surrounding context.\n"
            "- If apply_patch fails due to context, request more context and retry.\n\n"
            "## Output Format (strict)\n"
            "Thought: <your reasoning>\n"
            "Action: <tool name>\n"
            "Action Input: <tool input>\n\n"
            "System will reply with:\n"
            "Observation: <tool output>\n\n"
            "Repeat as needed, then finish with:\n"
            "Thought: I now know the final answer.\n"
            "Final Answer: <your final answer>"
        )
        sections.append(f"## Scratchpad\n{scratchpad_text}")
        return "\n\n".join(sections).strip()

    def _parse_reaction(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        thought_match = re.search(r"Thought:\s*(.+?)(?=\n(?:Action|Final Answer):|$)", text, re.DOTALL | re.IGNORECASE)
        action_match = re.search(r"Action:\s*(\w+)", text, re.IGNORECASE)
        action_input_match = re.search(r"Action Input:\s*(.+?)(?=\nObservation:|$)", text, re.DOTALL | re.IGNORECASE)
        final_answer_match = re.search(r"Final Answer:\s*(.+?)$", text, re.DOTALL | re.IGNORECASE)

        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        action_input = action_input_match.group(1).strip() if action_input_match else None
        final_answer = final_answer_match.group(1).strip() if final_answer_match else None

        return thought, action, action_input, final_answer

    def _append_stop_note(self, content: str) -> str:
        note = "[用户主动停止输出]"
        base = (content or "").rstrip()
        if not base:
            return note
        if base.endswith(note):
            return base
        return f"{base}\n\n{note}"

    def _get_tool(self, tools: List[Tool], name: Optional[str]) -> Optional[Tool]:
        if not name:
            return None
        return next((t for t in tools if t.name.lower() == name.lower()), None)

    def _safe_json_loads(self, value: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not value:
            return {}, None
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return data, None
            return None, "Tool arguments must be a JSON object."
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON arguments: {e}"

    def _extract_tool_input(self, tool: Tool, args: Dict[str, Any]) -> str:
        if not tool.parameters:
            return json.dumps(args) if args else ""
        if len(tool.parameters) == 1:
            key = tool.parameters[0].name
            value = args.get(key, "")
            return str(value)
        return json.dumps(args)

    def _prepare_tool_call(self, tools: List[Tool], tool_name: Optional[str], args_text: str) -> Tuple[Optional[Tool], str, Optional[str]]:
        tool = self._get_tool(tools, tool_name)
        args, parse_error = self._safe_json_loads(args_text)

        if tool is None:
            return None, "", f"Tool not found: '{tool_name}'"

        if parse_error:
            return tool, "", parse_error

        tool_input = self._extract_tool_input(tool, args or {})
        return tool, tool_input, None

    async def _execute_tool(self, tool: Tool, tool_input: str) -> str:
        try:
            return await tool.execute(tool_input)
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    async def _execute_tool_call(self, tools: List[Tool], tool_name: Optional[str], args_text: str) -> Tuple[str, str]:
        tool, tool_input, error_msg = self._prepare_tool_call(tools, tool_name, args_text)
        if error_msg:
            return tool_input, error_msg
        if tool is None:
            return tool_input, f"Tool not found: '{tool_name}'"
        output = await self._execute_tool(tool, tool_input)
        return tool_input, output

    def _build_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        input_items: List[Dict[str, Any]] = []

        def add_text(items: List[Dict[str, Any]], role: str, text: Any):
            if text is None:
                return
            text_value = str(text)
            if not text_value:
                return
            item_type = "output_text" if role == "assistant" else "input_text"
            items.append({"type": item_type, "text": text_value})

        def add_image(items: List[Dict[str, Any]], role: str, image_url: Any):
            if role == "assistant":
                return
            url = ""
            if isinstance(image_url, dict):
                url = image_url.get("url") or image_url.get("source") or ""
            elif isinstance(image_url, str):
                url = image_url
            if not url:
                return
            items.append({"type": "input_image", "image_url": {"url": url}})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            content_items: List[Dict[str, Any]] = []

            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        part_type = str(part.get("type", "") or "").lower()
                        if part_type in ("text", "input_text", "output_text"):
                            add_text(content_items, role, part.get("text") or part.get("content"))
                        elif part_type in ("image_url", "input_image"):
                            add_image(content_items, role, part.get("image_url"))
                        elif "text" in part:
                            add_text(content_items, role, part.get("text"))
                    else:
                        add_text(content_items, role, part)
            else:
                add_text(content_items, role, content)

            if not content_items:
                add_text(content_items, role, "")

            input_items.append({
                "type": "message",
                "role": role,
                "content": content_items
            })

        return input_items

    def _update_llm_processed(self, llm_call_id: int, payload: Dict[str, Any]) -> None:
        try:
            from database import db
            db.update_llm_call_processed(llm_call_id, payload)
        except Exception:
            pass

    def get_max_iterations(self) -> int:
        return self.max_iterations
