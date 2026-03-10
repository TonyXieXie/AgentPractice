# Memory / Request Building 概念对齐（Draft）
日期：2026-03-10

本文记录我们针对 `tauri-agent-next` 下一阶段（多 Agent + private 跨 run + 多 profile 多实例）在 **Prompt/Mem** 与 **LLM 请求装配** 方面的概念对齐与命名建议。

> 目标：让“记忆/上下文投影”与“不同 LLM API 请求体格式（chat completions / responses）”职责分离，避免 `ContextBuilder + PromptManager` 边界模糊。

---

## 1. 现状回顾（当前工程中的三个对象）

### 1.1 `LLMClient`
负责网络调用、重试、流式事件解析，并且当前也承担了一部分请求体装配：
- `openai_chat_completions`：`messages` + `max_tokens`
- `openai_responses`：`input` + `max_output_tokens` + `responses input` 结构转换
- 模型特殊参数（如 reasoning 参数）也在此处理

### 1.2 `ContextBuilder`
更像“外层适配/装配器”，当前同时做了两类事情：
- 解析 `AgentMessage` → `ExecutionRequest`（标准化内部执行请求）
- 创建 `LLMClient`、生成 `request_overrides`
- 构建 messages：委托给 Memory（v1 已接入 `AgentMemory`）；无 Memory 时使用 request.history fallback 拼接

### 1.3 `AgentMemory`（v1 已落地）
当前工程已将“Prompt IR + 压缩”能力落到 `AgentMemory`（代码：`python-backend/agents/execution/agent_memory.py`），用于替代早期的 `PromptManager` 实现。

v1 口径（与我们对齐一致）：
- 静态：agent profile 的基础提示词、静态工具定义/策略等
- 动态：
  - shared：Message Center 中“该 Agent 可接收”的消息历史（含 rpc_request/rpc_response）
  - private：该 Agent 自己的执行过程（工具调用记录等）
- 压缩/摘要：仅对 private 生效；shared 只做预算截断/丢弃兜底
- artifacts：只注入路径/索引，需要内容时再调用工具读取

---

## 2. 目标边界（我们希望的清晰分工）

### 2.1 目标
- **AgentMemory（per AgentInstance）**：
  - 记住：我是谁（type/profile/instance_id）、要干什么（task）、干了什么（facts）、得到了什么（artifacts refs）
  - 管理两类输入：
    - 静态：agent profile 的基础提示词、静态工具定义/策略等
    - 动态：Message Center 消息历史（shared）+ 本 Agent 私有执行过程（private）
  - 维护：shared/private 投影、跨 run 的摘要游标、截断/压缩策略（v1：摘要仅对 private 生效）
  - 输出：面向 LLM 的“规范化输入表示”（messages / PromptIR）
- **RequestBodyBuilder**：
  - 负责把 `AgentMemory` 的输出装配成某种 API 可直接发送的请求体
  - 适配 `chat completions` / `responses` 的字段结构差异
  - 处理模型特殊参数要求与 overrides 合并规则
- **LLMClient**：
  - B 方案最终形态：尽量变薄，专注 transport（发请求、重试、解析流式事件）
  - 渐进式 B’：外部调用 API 不变（仍用 `chat(messages, overrides)`），但内部引入 RequestBodyBuilder 让 payload 构建集中化

> 注意：执行层的 provider adapters（ReAct tool calling 协议差异）属于另一层，不应与 RequestBodyBuilder 重复职责。

---

## 3. 命名对齐（建议）

### 3.1 `AgentMemory`
- 每个 AgentInstance 一个 `AgentMemory` 实例
- private 归属以 `agent_instance_id` 为主键（跨 run 稳定）
- 支持同 profile 多实例（多个 `AgentMemory` 并存，各自 private 独立）

### 3.2 `RequestBodyBuilder`
- 以 `api_format` 为核心输入（如 `openai_chat_completions` / `openai_responses`）
- 不关心 session/可见性/压缩；只关心“如何序列化”

### 3.3 `LLMClient`
- 保留 `chat / chat_stream_events` 统一接口
- 内部通过 RequestBodyBuilder 生成 payload 与 endpoint，再发请求并产出统一事件

---

## 4. 关键设计点（与多 Agent/private 跨 run 强相关）

### 4.1 shared/private 投影必须在 Memory 层完成
- shared：来自 Message Center 且对该 AgentInstance 可接收的消息（v1：`rpc_request`/`rpc_response` 也算）
- private：只对 owner==当前 agent_instance_id 可见（tool_call/tool_result 默认 private）

Memory 构建 prompt 时只能看到：
- `shared` + `private(owner==self)`

说明：
- shared/private 只用于 **Memory 内部过滤与压缩**，不要求在最终 prompt 中显式标注“shared/private”

### 4.2 摘要/压缩状态必须按 agent_instance_id 拆分
原因：
- 防止 private 通过 summary 泄漏到 shared
- 支持不同 AgentInstance 的不同预算与压缩策略

v1 约定：
- 仅对 private 历史做滚动摘要；shared 历史不做摘要（只做预算丢弃/截断兜底）

### 4.3 产物默认不注入 prompt，但必须可发现
Memory 默认只注入 artifact refs（路径/hash/desc/created_by/task_id 等），不注入内容。
需要时由工具读取落盘产物。

### 4.4 v1 收敛点（已对齐）
- Memory 只需要单 Agent 视图（为某个 `agent_instance_id` 构建上下文）
- shared 历史的事实源：Message Center（包含 user input、agent output、RPC request/response 等）
- private 历史的事实源：该 AgentInstance 的私有执行记录（工具调用与中间笔记等）
- 摘要/压缩：仅对 private 生效；shared 只做预算丢弃/截断兜底
- artifacts：只注入路径/索引，不注入内容；需要内容时再通过工具读取

---

## 5. B 方案（最终形态）与 B’（渐进式）说明

### 5.1 B（最终）
- `LLMClient`：transport only
- `RequestBodyBuilder`：payload/endpoint 构建
- `AgentMemory`：prompt/memory view + compress/truncate

优点：
- 边界清晰、可测试性强
- 多 provider、多格式扩展更稳定

成本：
- 需要重构 `LLMClient` 内部的 payload 构建逻辑

### 5.2 B’（渐进式）
先保持 `LLMClient.chat(messages, overrides)` 接口不变：
- 仅把 payload 构建委托给 `RequestBodyBuilder`
- `LLMClient` 仍保留 stream 解析与 retry

优点：
- 对现有策略/执行链路侵入小

---

## 6. 接口草案（仅用于对齐）

### 6.1 AgentMemory
- `AgentMemory.build_view(session_id, agent_instance_id, task_ctx, llm_client/config, tool_policy) -> PromptIR`
- `PromptIR` 典型包含：
  - `messages`（规范化消息列表）
  - `budget`（prompt_budget/max_context_tokens/safety 等）
  - `trace`（压缩/截断/丢弃动作记录）

### 6.2 RequestBodyBuilder
- `RequestBodyBuilder.build(api_format, model_config, prompt_ir, overrides, tools, stream) -> (path, payload)`
  - `path`：`/chat/completions` 或 `/responses`
  - `payload`：可直接发送的 JSON body

---

## 7. 与现有 provider adapters 的边界

`agents/execution/providers/*` 当前负责：
- tool schema 注入（`tools` 字段）
- tool call / tool result 在 “messages” 中的表达方式差异（chat vs responses）

RequestBodyBuilder 不应重复这部分逻辑，只负责最终 HTTP payload 的结构与字段名差异。
