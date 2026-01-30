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
                
                # 🔥 调试：打印发送给 LLM 的详细信息
                print(f"\n{'='*80}")
                print(f"[ReAct Agent] Iteration {iteration + 1}/{self.max_iterations}")
                print(f"{'='*80}")
                print(f"📤 发送给 LLM 的消息:")
                print(f"\n[System Prompt]")
                print(f"{'-'*80}")
                print(prompt)
                print(f"{'-'*80}")
                print(f"\n[User Input]")
                print(f"{'-'*80}")
                print(user_input)
                print(f"{'-'*80}")
                print(f"\n⏳ 等待 LLM 响应...\n")
                
                response = await llm_client.chat(messages)
                llm_output = response.get("content", "")
                
                # 🔥 调试：打印 LLM 原始输出
                print(f"📥 LLM 原始输出:")
                print(f"{'-'*80}")
                print(llm_output)
                print(f"{'-'*80}\n")
                
            except Exception as e:
                yield AgentStep(
                    step_type="error",
                    content=f"LLM调用失败: {str(e)}",
                    metadata={"iteration": iteration, "error": str(e)}
                )
                return
            
            # Parse LLM output
            thought, action, action_input, final_answer = self._parse_reaction(llm_output)
            
            # 🔥 调试：打印解析结果
            print(f"🔍 解析结果:")
            print(f"{'-'*80}")
            print(f"  💭 Thought: {thought if thought else '❌ 未找到'}")
            print(f"  🔧 Action: {action if action else '❌ 未找到'}")
            print(f"  📝 Action Input: {action_input if action_input else '❌ 未找到'}")
            print(f"  ✅ Final Answer: {final_answer if final_answer else '❌ 未找到'}")
            print(f"{'-'*80}")
            print(f"{'='*80}\n")
            
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
        
        prompt = f"""你是一个具有推理和行动能力的AI助手。你需要通过"思考-行动-观察"的循环来解决问题。

## 可用工具
{tool_descriptions if tool_descriptions else "（当前没有可用工具）"}

## 回答格式（必须严格遵守）

你必须按照以下格式输出，每个步骤都要写：

```
Thought: [你的思考过程，分析问题需要什么]
Action: [工具名称]
Action Input: [工具的输入参数]
```

然后系统会返回：
```
Observation: [工具执行结果]
```

你可以重复上述步骤多次，直到获得足够信息。最后输出：
```
Thought: 我现在知道最终答案了
Final Answer: [你的最终答案]
```

## 重要规则
1. **必须先 Thought，再 Action** - 每次行动前都要思考
2. **Action 必须是上面列出的工具之一** - 不能编造工具
3. **Action Input 要简洁明确** - 直接给出参数，不要多余解释
4. **不要自己写 Observation** - Observation 由系统提供
5. **得出答案前必须说"我现在知道最终答案了"**

## 示例

### 示例1：计算问题
Question: 15乘以23加100等于多少？

Thought: 我需要计算15*23+100这个数学表达式
Action: calculator
Action Input: 15*23+100
Observation: 445
Thought: 我现在知道最终答案了
Final Answer: 15乘以23加100等于445

### 示例2：天气查询
Question: 北京今天天气怎么样？

Thought: 我需要查询北京的天气信息
Action: weather
Action Input: 北京
Observation: Beijing: Sunny, Temperature: 18°C, Humidity: 45%, Wind: 10 km/h
Thought: 我现在知道最终答案了
Final Answer: 北京今天天气晴朗，温度18°C，湿度45%，风速10公里/小时

### 示例3：多步骤问题
Question: 搜索一下人工智能，然后告诉我主要应用

Thought: 我需要先搜索人工智能的相关信息
Action: search
Action Input: 人工智能
Observation: Search results for '人工智能': 1. AI技术包括机器学习、深度学习... 2. 应用领域：医疗、金融、教育...
Thought: 我现在知道最终答案了
Final Answer: 人工智能的主要应用包括：医疗诊断、金融风控、智能教育、自动驾驶等领域

---

## 你之前的推理过程
{scratchpad_text if scratchpad_text else "（这是第一次推理，请开始思考）"}

---

现在请开始！记住：先 Thought，再 Action，严格遵循格式！"""
        
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
