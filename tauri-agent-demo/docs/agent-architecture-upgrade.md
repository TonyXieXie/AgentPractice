# Agent架构升级方案

## 目标

将当前的简单对话系统升级为支持多种Agent模式（ReAct、Tool Use、ToT等）的可扩展架构。

---

## 当前架构的局限性

### 1. **消息组装逻辑过于简单**
- 只支持简单的 System + History + User 模式
- 无法处理工具调用、中间推理步骤
- 不支持复杂的prompt engineering

### 2. **缺少Tool/Action抽象**
- 没有工具系统
- 无法让LLM调用外部API或函数

### 3. **缺少Agent执行引擎**
- 没有循环执行逻辑（ReAct需要多轮推理-行动循环）
- 无法管理复杂的状态机

---

## 推荐架构设计

### 核心概念层次

```
┌─────────────────────────────────────────────┐
│           前端 (React/TypeScript)            │
│  - 显示对话、工具调用、思考过程              │
└─────────────┬───────────────────────────────┘
              │ HTTP/SSE
┌─────────────▼───────────────────────────────┐
│          API Layer (FastAPI)                 │
│  - /chat (简单对话)                          │
│  - /chat/agent (Agent执行)                   │
│  - /tools (工具管理)                         │
└─────────────┬───────────────────────────────┘
              │
┌─────────────▼───────────────────────────────┐
│        Agent Orchestrator (核心)            │
│  - AgentExecutor                            │
│  - 策略选择器 (ReAct/Plan/ToT)              │
│  - 执行循环管理                             │
└─────────────┬───────────────────────────────┘
              │
        ┌─────┴─────┬─────────┬───────────┐
        │           │         │           │
┌───────▼──┐  ┌─────▼───┐  ┌─▼──────┐  ┌─▼─────┐
│ Message  │  │  Tool   │  │ Memory │  │  LLM  │
│ Builder  │  │ System  │  │ System │  │Client │
└──────────┘  └─────────┘  └────────┘  └───────┘
```

---

## 详细设计

### 1. Agent策略抽象层

创建策略基类，支持不同的Agent模式：

```python
# python-backend/agents/base.py

from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator
from dataclasses import dataclass

@dataclass
class AgentStep:
    """单个Agent执行步骤"""
    step_type: str  # "thought", "action", "observation", "answer"
    content: str
    metadata: Dict[str, Any] = None

class AgentStrategy(ABC):
    """Agent策略基类"""
    
    @abstractmethod
    async def execute(
        self,
        user_input: str,
        history: List[Dict],
        tools: List["Tool"],
        llm_client: "LLMClient"
    ) -> AsyncGenerator[AgentStep, None]:
        """
        执行Agent策略
        
        Yields:
            AgentStep: 每个执行步骤
        """
        pass
    
    @abstractmethod
    def build_prompt(
        self,
        user_input: str,
        history: List[Dict],
        tools: List["Tool"]
    ) -> str:
        """构建特定策略的prompt"""
        pass
```

### 2. 实现ReAct策略

```python
# python-backend/agents/react.py

import re
from typing import List, Dict, Any, AsyncGenerator
from .base import AgentStrategy, AgentStep

class ReActAgent(AgentStrategy):
    """
    ReAct (Reasoning + Acting) Agent
    
    循环执行：
    1. Thought: LLM思考下一步
    2. Action: 决定调用哪个工具
    3. Observation: 获取工具执行结果
    4. 重复直到得出最终答案
    """
    
    def __init__(self, max_iterations: int = 5):
        self.max_iterations = max_iterations
    
    async def execute(
        self,
        user_input: str,
        history: List[Dict],
        tools: List["Tool"],
        llm_client: "LLMClient"
    ) -> AsyncGenerator[AgentStep, None]:
        
        scratchpad = []  # 保存思考过程
        
        for iteration in range(self.max_iterations):
            # 构建prompt
            prompt = self.build_prompt(user_input, history, tools, scratchpad)
            
            # 调用LLM
            response = await llm_client.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_input}
            ])
            
            content = response["content"]
            
            # 解析LLM输出
            thought, action, action_input = self._parse_reaction(content)
            
            # Thought步骤
            if thought:
                yield AgentStep(
                    step_type="thought",
                    content=thought,
                    metadata={"iteration": iteration}
                )
                scratchpad.append(f"Thought: {thought}")
            
            # Action步骤
            if action:
                yield AgentStep(
                    step_type="action",
                    content=f"{action}: {action_input}",
                    metadata={"tool": action}
                )
                scratchpad.append(f"Action: {action}[{action_input}]")
                
                # 执行工具
                tool = self._get_tool(tools, action)
                if tool:
                    observation = await tool.execute(action_input)
                    
                    yield AgentStep(
                        step_type="observation",
                        content=observation,
                        metadata={"tool": action}
                    )
                    scratchpad.append(f"Observation: {observation}")
                else:
                    yield AgentStep(
                        step_type="error",
                        content=f"Tool '{action}' not found"
                    )
                    break
            
            # 检查是否得出最终答案
            if "Final Answer:" in content:
                final_answer = content.split("Final Answer:")[-1].strip()
                yield AgentStep(
                    step_type="answer",
                    content=final_answer
                )
                break
        
        # 如果达到最大迭代次数
        if iteration == self.max_iterations - 1:
            yield AgentStep(
                step_type="answer",
                content="抱歉，我无法在有限步骤内完成任务。"
            )
    
    def build_prompt(
        self,
        user_input: str,
        history: List[Dict],
        tools: List["Tool"],
        scratchpad: List[str] = None
    ) -> str:
        """构建ReAct prompt"""
        
        tool_descriptions = "\n".join([
            f"- {tool.name}: {tool.description}"
            for tool in tools
        ])
        
        scratchpad_text = "\n".join(scratchpad) if scratchpad else ""
        
        return f"""你是一个具有推理和行动能力的AI助手。

可用工具：
{tool_descriptions}

请按以下格式回答问题：

Thought: 你的推理过程
Action: 工具名称
Action Input: 工具输入
Observation: 工具返回的结果

（重复以上步骤直到你知道答案）

Thought: 我现在知道最终答案了
Final Answer: 最终答案

示例：
Question: 北京今天天气怎么样？
Thought: 我需要查询北京的天气信息
Action: weather_api
Action Input: 北京
Observation: 晴，温度15-25°C，空气质量良好
Thought: 我现在知道答案了
Final Answer: 北京今天天气晴朗，温度在15-25°C之间，空气质量良好。

之前的推理过程：
{scratchpad_text}

开始！
"""
    
    def _parse_reaction(self, text: str):
        """解析LLM的ReAct输出"""
        thought_match = re.search(r"Thought:\s*(.+?)(?=\n|Action:|$)", text, re.DOTALL)
        action_match = re.search(r"Action:\s*(\w+)", text)
        action_input_match = re.search(r"Action Input:\s*(.+?)(?=\n|Observation:|$)", text, re.DOTALL)
        
        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        action_input = action_input_match.group(1).strip() if action_input_match else None
        
        return thought, action, action_input
    
    def _get_tool(self, tools, name: str):
        """根据名称获取工具"""
        return next((t for t in tools if t.name == name), None)
```

### 3. Tool系统设计

```python
# python-backend/tools/base.py

from abc import ABC, abstractmethod
from typing import Any, Dict
from pydantic import BaseModel

class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str  # "string", "number", "boolean"
    description: str
    required: bool = True

class Tool(ABC):
    """工具基类"""
    
    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.parameters: List[ToolParameter] = []
    
    @abstractmethod
    async def execute(self, input_data: str) -> str:
        """
        执行工具
        
        Args:
            input_data: 工具输入
        
        Returns:
            工具执行结果
        """
        pass
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于LLM）"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.dict() for p in self.parameters]
        }
```

```python
# python-backend/tools/builtin.py

import httpx
from datetime import datetime
from .base import Tool, ToolParameter

class SearchTool(Tool):
    """网络搜索工具"""
    
    def __init__(self, api_key: str = None):
        super().__init__()
        self.name = "web_search"
        self.description = "搜索互联网获取最新信息"
        self.parameters = [
            ToolParameter(
                name="query",
                type="string",
                description="搜索关键词",
                required=True
            )
        ]
        self.api_key = api_key
    
    async def execute(self, query: str) -> str:
        # 实现实际的搜索逻辑
        # 这里是简化示例
        return f"关于'{query}'的搜索结果：..."

class WeatherTool(Tool):
    """天气查询工具"""
    
    def __init__(self):
        super().__init__()
        self.name = "weather"
        self.description = "查询指定城市的天气信息"
        self.parameters = [
            ToolParameter(
                name="city",
                type="string",
                description="城市名称",
                required=True
            )
        ]
    
    async def execute(self, city: str) -> str:
        # 调用天气API
        async with httpx.AsyncClient() as client:
            # 示例：调用和风天气API
            response = await client.get(
                f"https://api.qweather.com/v7/weather/now",
                params={"location": city, "key": "YOUR_API_KEY"}
            )
            data = response.json()
            return f"{city}天气：{data['now']['text']}, 温度{data['now']['temp']}°C"

class CalculatorTool(Tool):
    """计算器工具"""
    
    def __init__(self):
        super().__init__()
        self.name = "calculator"
        self.description = "执行数学计算"
        self.parameters = [
            ToolParameter(
                name="expression",
                type="string",
                description="数学表达式，如 '2+3*4'",
                required=True
            )
        ]
    
    async def execute(self, expression: str) -> str:
        try:
            # 安全的数学表达式求值
            result = eval(expression, {"__builtins__": {}}, {})
            return str(result)
        except Exception as e:
            return f"计算错误: {str(e)}"
```

### 4. Agent Executor（执行引擎）

```python
# python-backend/agents/executor.py

from typing import List, Dict, Optional
from .base import AgentStrategy, AgentStep
from .react import ReActAgent
from tools.base import Tool

class AgentExecutor:
    """Agent执行引擎"""
    
    def __init__(
        self,
        strategy: AgentStrategy,
        tools: List[Tool],
        llm_client: "LLMClient"
    ):
        self.strategy = strategy
        self.tools = tools
        self.llm_client = llm_client
    
    async def run(
        self,
        user_input: str,
        history: List[Dict] = None
    ):
        """
        执行Agent
        
        Yields:
            AgentStep: 每个执行步骤
        """
        history = history or []
        
        async for step in self.strategy.execute(
            user_input=user_input,
            history=history,
            tools=self.tools,
            llm_client=self.llm_client
        ):
            yield step

# 工厂函数
def create_agent_executor(
    agent_type: str,
    tools: List[Tool],
    llm_client: "LLMClient",
    **kwargs
) -> AgentExecutor:
    """创建Agent执行器"""
    
    if agent_type == "react":
        strategy = ReActAgent(max_iterations=kwargs.get("max_iterations", 5))
    elif agent_type == "simple":
        strategy = SimpleAgent()  # 当前的简单对话
    elif agent_type == "plan_execute":
        strategy = PlanExecuteAgent()  # 计划-执行模式
    elif agent_type == "tot":
        strategy = TreeOfThoughtsAgent()  # 思维树
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")
    
    return AgentExecutor(strategy, tools, llm_client)
```

### 5. 数据库Schema扩展

```sql
-- 会话表添加agent_type字段
ALTER TABLE chat_sessions ADD COLUMN agent_type TEXT DEFAULT 'simple';

-- 新增：工具调用记录表
CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    tool_output TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES chat_messages (id)
);

-- 新增：Agent步骤表（记录推理过程）
CREATE TABLE agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    step_type TEXT NOT NULL,  -- "thought", "action", "observation"
    content TEXT NOT NULL,
    metadata TEXT,  -- JSON
    sequence INTEGER NOT NULL,  -- 步骤顺序
    timestamp TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES chat_messages (id)
);
```

### 6. API接口更新

```python
# python-backend/main.py

@app.post("/chat/agent/stream")
async def chat_agent_stream(request: ChatRequest):
    """Agent模式的流式对话"""
    
    # 1. 获取会话和配置
    session = # ...
    config = # ...
    
    # 2. 获取工具列表
    tools = get_enabled_tools(session.id)
    
    # 3. 创建Agent执行器
    llm_client = create_llm_client(config)
    executor = create_agent_executor(
        agent_type=session.agent_type,
        tools=tools,
        llm_client=llm_client
    )
    
    # 4. 执行并流式返回
    async def event_generator():
        async for step in executor.run(request.message, history):
            # 保存步骤到数据库
            db.save_agent_step(step)
            
            # 流式返回
            yield {
                "data": {
                    "type": step.step_type,
                    "content": step.content,
                    "metadata": step.metadata
                }
            }
    
    return EventSourceResponse(event_generator())
```

### 7. 前端渲染适配

```typescript
// src/components/AgentStepView.tsx

interface AgentStepProps {
  step: {
    type: "thought" | "action" | "observation" | "answer";
    content: string;
    metadata?: any;
  };
}

export function AgentStepView({ step }: AgentStepProps) {
  switch (step.type) {
    case "thought":
      return (
        <div className="agent-step thought">
          <div className="step-icon">💭</div>
          <div className="step-content">{step.content}</div>
        </div>
      );
    
    case "action":
      return (
        <div className="agent-step action">
          <div className="step-icon">🔧</div>
          <div className="step-label">调用工具</div>
          <div className="step-content">{step.content}</div>
        </div>
      );
    
    case "observation":
      return (
        <div className="agent-step observation">
          <div className="step-icon">👁️</div>
          <div className="step-label">观察结果</div>
          <div className="step-content">{step.content}</div>
        </div>
      );
    
    case "answer":
      return (
        <div className="agent-step answer">
          <div className="step-icon">✅</div>
          <div className="step-content">{step.content}</div>
        </div>
      );
  }
}
```

---

## 实施路线图

### Phase 1: 基础架构 (1-2周)
- [ ] 创建Agent策略基类
- [ ] 创建Tool基类和工具注册系统
- [ ] 扩展数据库Schema
- [ ] 实现Simple Agent（兼容现有功能）

### Phase 2: ReAct实现 (1周)
- [ ] 实现ReActAgent策略
- [ ] 实现基础工具（Calculator, Weather, Search）
- [ ] 更新API接口支持Agent模式
- [ ] 前端适配Agent步骤显示

### Phase 3: 工具生态 (1-2周)
- [ ] 工具商店/注册中心
- [ ] 用户自定义工具
- [ ] 工具权限管理
- [ ] 工具使用统计

### Phase 4: 高级Agent (2-3周)
- [ ] Plan-Execute Agent
- [ ] Tree of Thoughts
- [ ] Multi-Agent协作
- [ ] Agent记忆系统优化

### Phase 5: 生产优化 (1周)
- [ ] 性能优化
- [ ] 错误处理完善
- [ ] 监控和日志
- [ ] 文档和测试

---

## 关键优势

1. **可扩展性**
   - 新的Agent策略只需实现基类接口
   - 新工具通过继承Tool基类轻松添加

2. **向后兼容**
   - SimpleAgent保持现有功能
   - 逐步迁移，不影响现有用户

3. **可观测性**
   - 完整的步骤记录
   - 便于调试和优化

4. **模块化**
   - Agent、Tool、Memory各司其职
   - 易于测试和维护

---

## 参考实现

推荐学习以下项目：
- **LangChain**: Agent抽象和工具系统
- **AutoGPT**: 自主Agent实现
- **BabyAGI**: 任务分解和执行
- **OpenAI Function Calling**: 工具调用规范
