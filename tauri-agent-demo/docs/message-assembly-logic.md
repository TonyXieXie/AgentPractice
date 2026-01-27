# 对话组装逻辑分析文档

## 概述

本文档详细分析了 Agent Chat 应用中如何将用户输入和历史对话组装成发送给 LLM 的消息格式。整个流程涉及消息预处理、历史消息管理、消息格式化等多个环节。

---

## 核心组件

### 1. MessageProcessor 类

位置：`python-backend/message_processor.py`

这是负责消息处理的核心类，提供以下关键方法：

```python
class MessageProcessor:
    - preprocess_user_message()    # 预处理用户输入
    - build_messages_for_llm()     # 构建 LLM 消息列表
    - postprocess_llm_response()   # 后处理 LLM 响应
    - format_history_for_display() # 格式化历史消息供前端显示
```

---

## 完整的消息处理流程

### 阶段 1: 用户发送消息

**前端** (`src/App.tsx` - `handleSend` 函数)

```typescript
// 用户在输入框输入消息并点击发送
const response = await sendMessage({
    message: userMessage,         // 用户输入的原始文本
    session_id: currentSessionId, // 当前会话ID（如果有）
    config_id: currentConfig?.id  // LLM配置ID
});
```

---

### 阶段 2: 后端接收请求

**后端API** (`python-backend/main.py` - `/chat` endpoint)

处理流程如下：

#### 步骤 1: 处理会话
```python
# 如果有 session_id，使用现有会话
if request.session_id:
    session = db.get_session(request.session_id)
else:
    # 否则创建新会话
    session = db.create_session(ChatSessionCreate(
        title="新对话",
        config_id=config_id
    ))
```

#### 步骤 2: 获取配置
```python
# 获取 LLM 配置（model、temperature、max_tokens等）
config = db.get_config(session.config_id)
```

#### 步骤 3: 预处理用户消息
```python
processed_message = message_processor.preprocess_user_message(request.message)

# preprocess_user_message 做了什么？
# 1. 清理空白字符（.strip()）
# 2. 移除多余的空行（最多保留2个连续换行）
# 3. 可扩展：添加特殊命令处理、上下文信息等
```

#### 步骤 4: 保存用户消息到数据库
```python
user_msg = db.create_message(ChatMessageCreate(
    session_id=session.id,
    role="user",
    content=processed_message
))
```

---

### 阶段 3: 组装 LLM 请求消息 ⭐核心

这是最关键的部分！

#### 步骤 5A: 获取历史消息
```python
# 从数据库获取最近20条消息
history = db.get_session_messages(session.id, limit=20)

# 转换为 LLM API 需要的格式
history_for_llm = [
    {"role": msg.role, "content": msg.content}
    for msg in history[:-1]  # 排除刚刚添加的用户消息
]
```

**示例数据：**
```python
history_for_llm = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么我可以帮助你的吗？"},
    {"role": "user", "content": "今天天气怎么样？"},
    {"role": "assistant", "content": "抱歉，我无法获取实时天气信息..."}
]
```

#### 步骤 5B: 构建完整的消息列表
```python
llm_messages = message_processor.build_messages_for_llm(
    user_message=processed_message,
    history=history_for_llm,
    system_prompt="你是一个有帮助的AI助手。"
)
```

**`build_messages_for_llm` 的内部逻辑：**

```python
def build_messages_for_llm(
    user_message: str,
    history: List[Dict[str, str]] = None,
    system_prompt: str = None,
    max_history: int = 10
) -> List[Dict[str, str]]:
    messages = []
    
    # 1. 首先添加系统提示词
    if system_prompt:
        messages.append({
            "role": "system", 
            "content": system_prompt
        })
    
    # 2. 添加历史消息（限制数量防止token过多）
    if history:
        recent_history = history[-max_history:] if len(history) > max_history else history
        messages.extend(recent_history)
    
    # 3. 最后添加当前用户消息
    messages.append({
        "role": "user", 
        "content": user_message
    })
    
    return messages
```

**组装后的最终消息格式：**

```python
llm_messages = [
    # 1. System prompt (如果有)
    {
        "role": "system",
        "content": "你是一个有帮助的AI助手。"
    },
    
    # 2. 历史消息 (最多10轮对话，即20条消息)
    {
        "role": "user",
        "content": "你好"
    },
    {
        "role": "assistant",
        "content": "你好！有什么我可以帮助你的吗？"
    },
    {
        "role": "user",
        "content": "今天天气怎么样？"
    },
    {
        "role": "assistant",
        "content": "抱歉，我无法获取实时天气信息..."
    },
    
    # 3. 当前用户新消息
    {
        "role": "user",
        "content": "那告诉我现在几点了"
    }
]
```

---

### 阶段 4: 发送到 LLM

#### 步骤 6A: 构建完整的请求数据
```python
raw_request_data = {
    "model": config.model,              # 例如 "gpt-4"
    "messages": llm_messages,           # 上面组装的消息列表
    "temperature": config.temperature,  # 例如 0.7
    "max_tokens": config.max_tokens,    # 例如 2000
    "api_type": config.api_type         # 例如 "openai"
}
```

#### 步骤 6B: 调用 LLM API
```python
llm_client = create_llm_client(config)
llm_result = await llm_client.chat(llm_messages)

# llm_result 的格式：
{
    "content": "现在是下午3点15分。",  # LLM 生成的文本
    "raw_response": {                  # 完整的 API 响应
        "id": "chatcmpl-xxx",
        "model": "gpt-4-0125-preview",
        "choices": [...],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 20,
            "total_tokens": 170
        }
    }
}
```

---

### 阶段 5: 处理响应

#### 步骤 7: 后处理 LLM 响应
```python
llm_response = llm_result["content"]
processed_response = message_processor.postprocess_llm_response(llm_response)

# postprocess_llm_response 做了什么？
# 1. 清理空白字符
# 2. 可选：移除 markdown 代码块标记
# 3. 可扩展：过滤敏感内容、格式化输出等
```

#### 步骤 8: 保存助手消息到数据库
```python
assistant_msg = db.create_message(ChatMessageCreate(
    session_id=session.id,
    role="assistant",
    content=processed_response,
    raw_request=raw_request_data,    # 保存原始请求（用于调试）
    raw_response=raw_response_data   # 保存原始响应（用于调试）
))
```

---

## 关键设计点

### 1. 历史消息限制

**为什么要限制？**
- LLM 有 token 限制（例如 GPT-4 是 8K 或 32K tokens）
- 太长的历史会消耗更多 token，增加成本
- 可能导致请求失败

**如何限制？**
```python
# 在 get_session_messages 时限制为20条
history = db.get_session_messages(session.id, limit=20)

# 在 build_messages_for_llm 时进一步限制为最近10轮对话
recent_history = history[-max_history:]  # max_history = 10
```

**实际效果：**
- 数据库获取：最多20条消息（10轮对话）
- 发送给 LLM：System prompt + 最多20条历史 + 1条新消息

---

### 2. 消息排除逻辑

```python
history_for_llm = [
    {"role": msg.role, "content": msg.content}
    for msg in history[:-1]  # ⚠️ 排除最后一条（刚添加的用户消息）
]
```

**为什么排除？**

因为流程是：
1. 先保存用户消息到数据库
2. 再从数据库获取历史
3. **此时数据库中已经包含了刚保存的用户消息**
4. 我们会在 `build_messages_for_llm` 中单独添加当前消息
5. 如果不排除，就会重复添加！

---

### 3. 消息顺序

LLM 消息的标准顺序：

```
1. System Message (可选，只能有一条)
   ↓
2. 历史对话 (User → Assistant → User → Assistant...)
   ↓
3. 当前 User Message
```

**错误示例：**
```python
# ❌ 错误：system 在中间
[
    {"role": "user", "content": "..."},
    {"role": "system", "content": "..."},  # 位置错误
    {"role": "user", "content": "..."}
]
```

**正确示例：**
```python
# ✅ 正确：system 在最前面
[
    {"role": "system", "content": "..."},  # 必须第一个
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "..."}
]
```

---

## 数据流图

```
用户输入
    ↓
[前端] 发送请求
    ↓
[后端] 接收 ChatRequest {message, session_id, config_id}
    ↓
[1] 获取/创建会话
    ↓
[2] 获取 LLM 配置
    ↓
[3] 预处理用户消息 (清理空白、移除多余换行)
    ↓
[4] 保存用户消息到数据库
    ↓
[5] 从数据库获取历史消息 (limit=20)
    ↓
[6] 转换历史消息格式 (排除刚添加的消息)
    ↓
[7] 组装 LLM 消息列表:
    - System prompt
    - 历史消息 (最多10轮)
    - 当前用户消息
    ↓
[8] 调用 LLM API
    ↓
[9] 后处理响应 (清理空白)
    ↓
[10] 保存助手消息到数据库 (包含调试数据)
    ↓
[前端] 接收响应并重新加载消息列表
    ↓
显示给用户
```

---

## 实际示例

假设用户正在进行第三次对话：

**数据库中的历史：**
```
ID 1: user      - "你好"
ID 2: assistant - "你好！有什么可以帮你？"
ID 3: user      - "今天天气怎么样？"
ID 4: assistant - "抱歉，我无法获取天气..."
```

**用户新输入：**
```
"那告诉我现在几点了"
```

**处理流程：**

1. **保存用户消息到数据库** (ID 5)
2. **获取历史** (ID 1-5，共5条)
3. **排除最后一条** (ID 1-4，共4条)
4. **组装消息：**

```python
[
    {
        "role": "system",
        "content": "你是一个有帮助的AI助手。"
    },
    {
        "role": "user",
        "content": "你好"
    },
    {
        "role": "assistant",
        "content": "你好！有什么可以帮你？"
    },
    {
        "role": "user",
        "content": "今天天气怎么样？"
    },
    {
        "role": "assistant",
        "content": "抱歉，我无法获取天气..."
    },
    {
        "role": "user",
        "content": "那告诉我现在几点了"  # 当前新消息
    }
]
```

5. **发送给 LLM**
6. **LLM 返回：** "现在是下午3点15分。"
7. **保存助手响应** (ID 6)

---

## 扩展点和优化建议

### 1. 智能历史管理

当前实现：简单的数量限制（最后20条）

**可以改进为：**
- Token 计数限制（更精确）
- 重要性评分（保留关键对话）
- 摘要压缩（总结旧对话）

### 2. 上下文增强

可以在 `build_messages_for_llm` 中添加：
- 用户资料信息
- 当前时间/日期
- 会话元数据（创建时间、主题等）

### 3. 预处理增强

可以在 `preprocess_user_message` 中添加：
- 检测特殊命令（如 `/clear`, `/help`）
- 自动纠错
- 敏感词过滤

### 4. 流式响应

当前是等待完整响应，可以改为：
- SSE (Server-Sent Events) 流式返回
- WebSocket 实时推送
- 提升用户体验

---

## 调试技巧

### 查看实际发送给 LLM 的消息

在 Debug 面板中查看 `raw_request` 字段：

```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    ...
  ],
  "temperature": 0.7,
  "max_tokens": 2000
}
```

### 使用日志

```python
# 在 main.py 中添加：
print(f"发送给 LLM 的消息数量: {len(llm_messages)}")
print(f"消息内容: {llm_messages}")
```

---

## 总结

**消息组装的核心原则：**

1. ✅ **System prompt 在最前面**
2. ✅ **历史消息按时间顺序**
3. ✅ **当前消息在最后**
4. ✅ **控制总长度避免超出 token 限制**
5. ✅ **预处理和后处理保证数据质量**

**关键代码文件：**
- `message_processor.py` - 消息处理逻辑
- `main.py` - 完整的处理流程
- `llm_client.py` - LLM API 调用

**注意事项：**
- 排除刚保存的用户消息避免重复
- 限制历史消息数量避免 token 超限
- 保存原始数据用于调试和分析
