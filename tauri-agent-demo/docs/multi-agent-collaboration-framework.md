# Multi-Agent 协作框架（消息 + 文件）

## 1. 定义与边界

本文件采用以下定义：

- 多 agent 协作不等于当前 `subagent` 机制。
- 多 agent 的核心是“平级 Agent 实例 + 任务编排”，不是“父子会话”。
- `subagent` 可作为过渡兼容层，但不应成为目标架构中心。

---

## 2. 目标模型（本轮确认）

系统目标是以下 4 点：

1. 整个系统可从 `Agent Profile` 创建不同的 `Agent Instance`。
2. 各 `Agent Instance` 之间是平等状态，不存在固定父子层级。
3. 系统以任务驱动，可按任务调度任意 Agent 实例执行。
4. 任一 Agent 完成任务后，可发起下一步任务并交接给其他 Agent。

这意味着：编排核心从“会话树”转为“任务图（Task Graph）”。

---

## 3. 与现有 subagent 的关系

当前 `spawn_subagent` 机制可以继续使用，但只建议定位为：

- Legacy Adapter（旧接口兼容层）
- 或 `task_spawn` 的一类实现

不建议再把核心语义建立在：

- `parent_session_id`
- `subagent_started/subagent_done`
- “父消息回写子结果”的固定流程

---

## 4. 核心架构要点（平级多 Agent）

### 4.1 运行时实体

建议把运行时拆成 5 个一等实体：

- `AgentProfile`：能力定义（工具、约束、提示模板）
- `AgentInstance`：可调度执行体（由 Profile 实例化）
- `Task`：独立任务单元（输入、状态、产物）
- `Artifact`：文件与中间结果引用（路径 + 版本锚点）
- `HandoffEvent`：Agent 之间的结构化交接事件

### 4.2 通信协议

建议所有 Agent 间通信都采用结构化 `handoff`，最少包含：

- `task_id`
- `from_agent_instance_id`
- `to_agent_instance_id`（可空，表示由调度器选择）
- `intent`
- `artifacts`
- `constraints`
- `status`

同时保留一份人类可读文本摘要，便于调试和 UI 展示。

### 4.3 任务状态机

任务状态建议统一为：

- `pending`
- `running`
- `blocked`
- `succeeded`
- `failed`
- `cancelled`

每次状态变化都落事件表，禁止仅依赖内存状态。

### 4.4 文件协作与版本一致性

“文件路径 + 描述”不够，必须带版本锚点。建议：

- 每次 handoff 携带 `tree_hash` 或 commit hash。
- Artifact 存 `path + checksum + produced_by_task_id`。
- 测试类 Agent 总是基于明确版本执行。

### 4.5 上下文隔离

单个 Agent 实例的执行上下文建议独立：

- `work_path`
- `extra_work_paths`
- 工具权限策略
- token/context 配额

避免把主会话上下文隐式继承到所有实例。

### 4.6 回滚语义

回滚应基于“任务关系”而不是文本解析。建议：

- 维护 `task_edges`（任务依赖）
- 回滚按任务图裁剪受影响分支
- 文件恢复基于任务版本锚点

---

## 5. 对当前代码的关键改造点

### 5.1 数据模型

建议新增：

- `agent_instances`
- `agent_tasks`
- `agent_task_events`
- `agent_artifacts`
- `agent_task_edges`

并将 `parent_session_id` 从核心关联降级为兼容字段。

### 5.2 API 与编排

建议新增任务中心 API：

- `POST /tasks`
- `POST /tasks/{id}/handoff`
- `POST /tasks/{id}/cancel`
- `GET /tasks/{id}`
- `GET /tasks/{id}/events`

`/chat/agent/stream` 可保留为“交互入口”，内部转任务执行。

### 5.3 WS 事件

建议 WS 事件从 `subagent_*` 扩展为任务事件：

- `task_started`
- `task_progress`
- `task_handoff`
- `task_completed`
- `task_failed`

并默认使用显式订阅，避免 `*` 全量噪声。

---

## 6. 当前线程模型与多线程决策

### 6.1 当前运行模型

当前主要是单进程 asyncio 事件循环并发：

- Agent 执行通过 `asyncio.create_task(...)` 并发调度。
- 这不是“每个 agent 一个线程”。
- 代码里有 `to_thread/subprocess` 用于部分阻塞点下沉。

### 6.2 是否要上多线程

结论：短期不建议“每个 Agent 一个线程”。

原因：

- 当前核心瓶颈更可能是任务状态一致性与调度，而非线程数。
- Python GIL 对 CPU 并行帮助有限。
- 线程会放大共享状态复杂度。

建议顺序：

1. 先完成任务中心与协议重构。
2. 在 asyncio 下做并发限流与背压控制。
3. 把 CPU 重负载节点下沉到进程池或独立 worker。
4. 再评估多进程部署（通常优于多线程）。

---

## 7. 推荐落地顺序（按风险最小）

1. 固化 `handoff` schema 与任务状态机。
2. 建立任务与产物持久化表。
3. 实现任务中心 API 与 WS 任务事件。
4. 将现有 `spawn_subagent` 映射到任务 API（兼容期）。
5. 去除核心流程对 `parent_session_id` 的依赖。
6. 完成并发限流、取消传播、失败重试策略。
7. 压测后决定是否引入多进程 worker。

---

## 8. 一句话结论

你定义的“平级 Agent + 任务图编排”是正确方向；`subagent` 可以保留兼容，但不应继续作为核心语义。
