"""
ReActAgent - Reasoning + Acting Agent

Implements the ReAct pattern (Yao et al., 2022):
1. Thought: LLM reasons about the problem
2. Action: LLM decides which tool to use
3. Observation: Tool execution result
4. Repeat until final answer is reached

Paper: https://arxiv.org/abs/2210.03629
"""

import re
from typing import List, Dict, Any, AsyncGenerator, Optional
from .base import AgentStrategy, AgentStep
from tools.base import Tool


class ReActAgent(AgentStrategy):
    """
    ReAct (Reasoning + Acting) Agent.
    
    Iteratively:
    - Thinks about the next step
    - Takes an action (uses a tool)
    - Observes the result
    - Continues until reaching a final answer
    """
    
    def __init__(self, max_iterations: int = 5):
        """
        Initialize ReActAgent.
        
        Args:
            max_iterations: Maximum number of thought-action-observation cycles
        """
        self.max_iterations = max_iterations
    
    async def execute(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        llm_client: "LLMClient",
        session_id: Optional[str] = None
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute ReAct loop.
        
        Args:
            user_input: User's question/request
            history: Conversation history
            tools: Available tools
            llm_client: LLM client
            session_id: Optional session ID
        
        Yields:
            AgentStep for each thought, action, observation, and final answer
        """
        scratchpad = []  # Track reasoning history
        
        for iteration in range(self.max_iterations):
            # Build prompt with current scratchpad
            prompt = self.build_prompt(user_input, history, tools, {
                "scratchpad": scratchpad,
                "iteration": iteration
            })
            
            # Call LLM
            try:
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input}
                ]
                
                response = await llm_client.chat(messages)
                llm_output = response.get("content", "")
                
            except Exception as e:
                yield AgentStep(
                    step_type="error",
                    content=f"LLM调用失败: {str(e)}",
                    metadata={"iteration": iteration, "error": str(e)}
                )
                return
            
            # Parse LLM output
            thought, action, action_input, final_answer = self._parse_reaction(llm_output)
            
            # Check for final answer first
            if final_answer:
                yield AgentStep(
                    step_type="answer",
                    content=final_answer,
                    metadata={
                        "agent_type": "react",
                        "iterations": iteration + 1,
                        "scratchpad": scratchpad
                    }
                )
                return
            
            # Emit thought step
            if thought:
                yield AgentStep(
                    step_type="thought",
                    content=thought,
                    metadata={"iteration": iteration}
                )
                scratchpad.append(f"Thought: {thought}")
            
            # Handle action
            if action and action_input:
                # Emit action step
                yield AgentStep(
                    step_type="action",
                    content=f"{action}[{action_input}]",
                    metadata={"tool": action, "input": action_input, "iteration": iteration}
                )
                scratchpad.append(f"Action: {action}")
                scratchpad.append(f"Action Input: {action_input}")
                
                # Execute tool
                tool = self._get_tool(tools, action)
                if tool:
                    try:
                        observation = await tool.execute(action_input)
                        
                        # Emit observation step
                        yield AgentStep(
                            step_type="observation",
                            content=observation,
                            metadata={"tool": action, "iteration": iteration}
                        )
                        scratchpad.append(f"Observation: {observation}")
                        
                    except Exception as e:
                        error_msg = f"工具执行失败: {str(e)}"
                        yield AgentStep(
                            step_type="observation",
                            content=error_msg,
                            metadata={"tool": action, "error": str(e), "iteration": iteration}
                        )
                        scratchpad.append(f"Observation: {error_msg}")
                else:
                    error_msg = f"未找到工具 '{action}'"
                    yield AgentStep(
                        step_type="error",
                        content=error_msg,
                        metadata={"tool": action, "iteration": iteration}
                    )
                    scratchpad.append(f"Observation: {error_msg}")
            else:
                # LLM didn't provide action - might be confused
                yield AgentStep(
                    step_type="thought",
                    content="(Agent未能确定下一步行动)",
                    metadata={"iteration": iteration, "warning": "no_action"}
                )
        
        # Reached max iterations without final answer
        yield AgentStep(
            step_type="answer",
            content="抱歉，我在有限的步骤内未能完成任务。请尝试重新表述您的问题或将其分解为更简单的子问题。",
            metadata={
                "agent_type": "react",
                "iterations": self.max_iterations,
                "max_iterations_reached": True
            }
        )
    
    def build_prompt(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        tools: List[Tool],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build ReAct prompt with tool descriptions and examples.
        
        Args:
            user_input: User's question
            history: Conversation history (not used in basic ReAct)
            tools: Available tools
            additional_context: Dict with 'scratchpad' and 'iteration'
        
        Returns:
            Formatted ReAct prompt
        """
        # Build tool descriptions
        tool_descriptions = "\n".join([
            f"- {tool.name}: {tool.description}"
            for tool in tools
        ])
        
        # Get scratchpad if available
        scratchpad = additional_context.get("scratchpad", []) if additional_context else []
        scratchpad_text = "\n".join(scratchpad) if scratchpad else ""
        
        prompt = f"""你是一个具有推理和行动能力的AI助手。你可以通过以下步骤解决问题：

可用工具：
{tool_descriptions if tool_descriptions else "（当前没有可用工具）"}

请严格按照以下格式回答问题：

Thought: 你对问题的思考和推理
Action: 工具名称
Action Input: 工具的输入参数
Observation: 工具返回的结果

（重复以上步骤直到你知道答案）

Thought: 我现在知道最终答案了
Final Answer: 最终答案

**重要规则：**
1. 每次只能使用一个工具
2. Action必须是上面列出的工具之一
3. Action Input应该是简洁明确的参数
4. 在得出Final Answer之前，必须先说"我现在知道最终答案了"

**示例1（使用计算器）：**
Question: 15乘以23加100等于多少？
Thought: 我需要计算15*23+100
Action: calculator
Action Input: 15*23+100
Observation: 445
Thought: 我现在知道最终答案了
Final Answer: 15乘以23加100等于445

**示例2（查询天气）：**
Question: 北京今天天气怎么样？
Thought: 我需要查询北京的天气
Action: weather
Action Input: 北京
Observation: Beijing: Sunny, Temperature: 18°C, Humidity: 45%, Wind: 10 km/h
Thought: 我现在知道最终答案了
Final Answer: 北京今天天气晴朗，温度18°C，湿度45%，风速10公里/小时。

之前的推理过程：
{scratchpad_text if scratchpad_text else "（这是第一次推理）"}

现在开始！严格遵循格式。"""
        
        return prompt
    
    def _parse_reaction(self, text: str):
        """
        Parse LLM output to extract thought, action, action_input, and final_answer.
        
        Args:
            text: LLM output text
        
        Returns:
            Tuple of (thought, action, action_input, final_answer)
        """
        # Extract components using regex
        thought_match = re.search(r"Thought:\s*(.+?)(?=\n(?:Action|Final Answer):|$)", text, re.DOTALL | re.IGNORECASE)
        action_match = re.search(r"Action:\s*(\w+)", text, re.IGNORECASE)
        action_input_match = re.search(r"Action Input:\s*(.+?)(?=\nObservation:|$)", text, re.DOTALL | re.IGNORECASE)
        final_answer_match = re.search(r"Final Answer:\s*(.+?)$", text, re.DOTALL | re.IGNORECASE)
        
        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        action_input = action_input_match.group(1).strip() if action_input_match else None
        final_answer = final_answer_match.group(1).strip() if final_answer_match else None
        
        return thought, action, action_input, final_answer
    
    def _get_tool(self, tools: List[Tool], name: str) -> Optional[Tool]:
        """
        Get tool by name.
        
        Args:
            tools: List of available tools
            name: Tool name to find
        
        Returns:
            Tool instance or None if not found
        """
        return next((t for t in tools if t.name.lower() == name.lower()), None)
    
    def get_max_iterations(self) -> int:
        """Get max iterations for ReAct"""
        return self.max_iterations
