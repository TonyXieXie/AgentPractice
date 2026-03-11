# tauri-agent-next 概念对齐（Glossary + Boundary）
日期：2026-03-11

本文用于对齐 `tauri-agent-next` 的核心概念与边界，作为后续多 Agent / 持久化 / Prompt 投影实现的共同语言。本文描述的是**目标架构**，不是当前代码实现说明。

## 1. 目标与原则

### 1.1 目标
- 支持多 Agent 角色（`planner/coder/reviewer/...`）在同一用户会话内协作
- 支持 private 上下文跨 run 复用（私有记忆/执行痕迹可持续）
- 支持“对话共享、执行私有、产物落盘共享（默认不入 prompt）”

### 1.2 核心原则
- **Shared Conversation**：对话过程以 **Message Center** 为事实源，按目标投递或 topic 订阅对相关 Agent 可见
- **Private Execution**：某个 Agent 的执行过程与工具结果默认仅自己可见（private）
- **Artifacts**：产物通常以落盘方式共享，默认不注入上下文；只共享“引用/索引”（path/hash/desc 等）

> 这里的 shared/private 是 “对 session 内 Agent 的可见性”，不等价于“是否对用户 UI 可见”。

---

## 2. 概念定义（推荐口径）

### 2.1 `session`（用户对话会话）
一个用户对话线程（thread），跨多次用户请求复用上下文的容器。

- 主键：`session_id`
- 生命周期：用户开始对话 → 多次 run → 用户结束/归档
- 职责：
  - 存储共享对话事实（shared）与可选的私有记忆（private）
  - 存储默认配置（默认 `llm_config/system_prompt/work_path` 等）
  - 持久化 Agent roster（同 session 下有哪些 AgentInstance）

### 2.2 `run`（一次执行作业）
一次执行作业（job）。run 以可观测、可取消、可回放为目标。  
外部入口（如 HTTP `POST /runs`、`POST /runs/{run_id}/stop`）在概念上先进入 `AgentCenter`，再交由 `UserProxyAgent` 处理；当 `UserProxyAgent` 决定启动、继续或停止一次执行时，会关联、打开或终止对应的 `run`。

- 主键：`run_id`
- 生命周期：`run.started` →（多次 LLM/tool/step）→ `run.finished|run.error|stopped`
- 边界：
  - `run` 是控制/观测单位，不等价于 session
  - 同一 session 下可以有多个 run
  - `run` 的生命周期由 `RunManager` 托管，但 `RunManager` 不直接拥有用户请求语义

### 2.3 `task`（Agent 本地托管的工作项）
`task` 不是跨 Agent 协作协议，也不是执行核心；它更接近某个 AgentInstance 在本地托管的一段可恢复、可观测的工作。

推荐特性：
- 主键：`task_id`
- 归属：`session_id` +（可选）`run_id`
- 宿主：`hosted_by_agent_instance_id`
- 来源：一个触发执行的 `AgentMessage`（如外部用户请求、RPC request/response、event）
- 状态：`queued|running|completed|failed|stopped`
- `depends_on_task_ids` 不作为 v1 主协作模型；跨 Agent 依赖优先通过 `rpc request/response` 表达

关系建议：
- 1 run 内可以出现 0..N 个 local task
- 1 task 只由 1 个 AgentInstance 串行执行（见 2.6）
- 一个 Agent 做完当前自己内部能完成的工作后，该 local task 即可结束
- 如果需要其它 Agent 协助，当前 Agent 发送 `rpc request` 后可以结束当前 local task；后续 `rpc response` 作为新消息，再决定是否托管新的 local task

### 2.4 `iteration`（一次 ReAct 循环单元）
模型内部一次 ReAct 回合（你之前称 round），建议统一叫 `iteration`。

语义：
- 1 task 可以包含 0..N 次 iteration
- 每次 iteration 通常是：LLM 推理/产出 tool_call → tool_result → 继续推理/收敛

### 2.5 `step`（观测层最小颗粒）
用于 UI/回放/流式展示的最细粒度事件片段（例如 `answer_delta/tool.started/...`）。

注意：
- step 是观测/流式单位，不建议直接当作 prompt 记忆的事实源（噪声大、体量大）

### 2.6 `AgentInstance`（可持久化的“工人”）
一个 session 内可持久化的 Agent 执行单元（actor/worker）。它有稳定身份，用于跨 run 私有上下文归属。

约束与能力：
- 主键：`agent_instance_id`（即 `AgentInstance.id`，在 session 内唯一）
- 允许：**同一个 profile 可以有多个 AgentInstance**
- 执行模型：**单个 AgentInstance 串行执行 task**
- 并发模型：需要并发且无显式依赖时，创建更多 AgentInstance 以并行处理

> `AgentInstance` 是“逻辑实体”，需要落库以支持反序列化与跨 run 复用；不是 Python 进程内对象的持久化。

### 2.7 `AgentMessage`（统一协作协议）
所有 Agent 之间的协作，都通过统一的 `AgentMessage` 进行。

- **消息主类型**只有两种：
  - `rpc`：需要对方返回消息；包含 `request` 与 `response` 两个方向
  - `event`：不要求返回消息
- **消息对象**只有两种：
  - `target`：定向消息；可面向具体 `AgentInstance`，也可面向某个 `AgentProfile`
  - `broadcast`：广播消息；必须带 `topic`，并发送给所有订阅该 topic 的对象
- `target` 与 `broadcast` 是投递对象语义，`rpc` 与 `event` 是协作语义；两者是正交维度

### 2.8 `message center`（消息中心）
用于 session 内的 **统一通信与可查询事实源**（inbox/outbox）。

- 所有外部请求、Agent 输出、RPC request/response、event、broadcast 都应通过 Message Center 投递与持久化
- 对某个 AgentInstance 来说：它的 **shared 历史** = Message Center 中“该 Agent 可接收（可路由/订阅命中）”的消息集合
- `target` 消息对发送方与被投递目标可见；`broadcast(topic)` 只对命中该 topic 订阅的对象可见
- 这意味着 shared 不一定是“所有 Agent 都看到同一份”；是否可见取决于目标投递或订阅命中，而不是 session 全员默认共享

---

## 3. Agent 相关概念

### 3.1 `agent type`（实现类型）
描述 Agent 的实现/能力边界，用于路由与执行方式：
- `assistant`
- `user_proxy`

> v1 不单独引入 `orchestrator` 类型；协作/编排通过普通 Agent 的消息收发与 RPC 完成。

### 3.2 `agent profile`（协作角色）
描述 Agent 的协作职责与提示词/权限配置：
- `planner`
- `coder`
- `reviewer`

profile 典型包含：
- system prompt 片段（persona/规则/格式）
- 工具可用范围与工具策略
- 默认订阅的 broadcast topics
- 预算与压缩策略（token budget / truncation / summarize）
- 产物共享策略（落盘目录、manifest 规则等）

### 3.3 `topic subscription`（广播订阅）
描述某个 AgentInstance 会接收哪些 `broadcast(topic)` 消息。

- v1 中，订阅集合的默认来源是 `AgentProfile`
- `AgentInstance` 默认继承所属 profile 的订阅 topic 集
- 运行时动态增删订阅不在本文范围内；如后续引入，应视为扩展能力

### 3.4 `agent id`
此处约定：`agent id` 指 **AgentInstance 的稳定 id**（即 `agent_instance_id`）。

> 如果运行时还需要“run 内临时实例 id”，建议另起名字（例如 `runtime_agent_id`），避免混淆。

### 3.5 `AgentCenter`
系统内统一的消息入口与路由中心。

- 接收外部入口消息与 Agent 间消息
- 负责把外部请求交给 `UserProxyAgent`
- 负责 target 路由与 broadcast topic 订阅匹配
- 负责与 Message Center 持久化事实源对接

### 3.6 `UserProxyAgent`
面向人类输入的代理 Agent。

- 接收来自 `AgentCenter` 的外部请求消息
- 负责把外部用户/控制请求转换为系统内部可协作的 `AgentMessage`
- 负责决定下一步是启动 run、拒绝、继续会话，还是停止某个 run

### 3.7 `RunManager`
run 生命周期宿主。

- 负责 `run.started / run.finished / stop`
- 负责 active run registry 与 run 观测状态
- 不负责外部用户请求入口，也不负责一般消息路由

---

## 4. 可见性模型（Shared vs Private）

### 4.1 shared（共享）
对某个 AgentInstance 来说，shared 历史来自 **Message Center**（并且该 Agent 能接收到）。

常见包含：
- 用户输入与需求变更（统一走 Message Center）
- 其它 Agent 的结论、约束、决策、计划变更
- `rpc request / rpc response`（只要该 Agent 是目标、调用方，或后续路由需要它可见）
- `event`（包括 target event 与命中 topic 订阅的 broadcast event）
- 产物引用/索引（artifact refs，内容落盘）

可见性规则：
- `target` 消息：对发送方与解析后的目标可见
- `broadcast(topic)`：仅对订阅该 topic 的 AgentInstance 可见

### 4.2 private（私有）
只对某个 AgentInstance 可见的记录，默认用于保存执行细节与工具输出：
- `tool_call` / `tool_result`（默认 private）
- 调试信息、执行过程笔记（如需要）
- 该 Agent 的私有滚动摘要（private summary）

private 记录必须带：
- `owner_agent_instance_id`

> 注：shared/private 是 Memory 层的过滤维度，不要求在最终 prompt 中显式标注。

### 4.3 共享对话 vs 共享产物
你们的目标是：
- **对话共享**：共享事实进入 shared
- **产物共享**：产物内容落盘，shared 中只记录引用，不默认注入 prompt

这要求系统提供：
- artifact 的落盘工具能力（写文件/打 patch/产物目录）
- artifact 的索引能力（path/hash/desc/created_by/...）

---

## 5. 事件与存储边界（建议）

### 5.1 run 事件（观测/回放）
用于观测与回放的细粒度事件流（例如 `ExecutionEvent`），建议按 `run_id` 存储，包含：
- run.started / run.finished
- llm/tool 的 chunk 与状态更新

特点：
- 体量大、噪声大
- 不作为 prompt 事实源（最多作为调试引用）

### 5.2 session 事件（上下文事实源）
用于重建上下文与跨 run 复用的事实源，建议存 SQLite（或等价的可查询存储）：
- shared：Message Center 的消息日志（按“当前 Agent 是 target 命中方或 broadcast 订阅命中方”投影进 Memory）
- private：owner agent 的私有记忆/执行记录（例如工具调用过程/中间笔记）

特点：
- 需要可投影（按 viewer 过滤 private）
- 需要可压缩（summary/cursor）；v1 约定 **摘要仅对 private 生效**，避免 private → shared 泄漏

### 5.3 artifacts（落盘共享）
产物内容落盘（work_path 或 runtime data dir），在 DB 中只存引用/索引。

默认策略：
- Prompt 不注入产物内容
- 需要时由 Agent 通过工具读取/检索产物

---

## 6. Prompt 构建视图（按 AgentInstance）

对某个 AgentInstance 构建 prompt 时，建议使用“投影视图”：

1) System
- base system prompt
- agent profile prompt（planner/coder/reviewer）
- agent instance override（可选）

2) History（事实源）
- shared 历史：Message Center 中该 Agent 可接收的消息
- private 历史：仅 owner==当前 agent_instance_id 的私有执行记录
- 产物只注入引用（artifact refs），不注入内容

3) Current input
- 当前触发本轮执行的 `AgentMessage`
- 可选的本地 `task` 托管上下文（如 `work_path`、local objective、retry state）

4) Budget/Compress
- truncation（对长文本、tool 输出等）
- summarize（v1：仅对 private 历史做滚动摘要，按 agent_instance_id 分开维护）
- drop（兜底，保留 system + summary + 最近 N 条）

---

## 7. 并发与调度（建议）

### 7.1 串行约束
- 同一个 `agent_instance_id` 同时只能执行一个 task（队列/锁）

### 7.2 并发扩容
- 当需要并发且任务间无显式依赖：创建更多 AgentInstance（同 profile 多实例）
- 跨 Agent 协作通过 `rpc request/response` 触发，不通过中心化 orchestrator 分派子任务
- `TaskManager` 负责托管/停止/完成本地 task，不负责维护 child-task DAG

### 7.3 资源冲突（必须提前考虑）
即使 task 无显式依赖，也可能因共享资源冲突（尤其是同一 `work_path` 的写操作）。

建议最小策略：
- 默认“单写者”或对写工具加互斥（work_path 级写锁）
- 允许并发执行读/分析类 task

---

## 8. 关系图与执行流程（概念）

### 8.1 概念关系

```mermaid
flowchart TD
  session["session (session_id)"] --> roster["Agent roster (AgentInstance*)"]
  roster --> profiles["AgentProfile / default subscriptions"]
  session --> mc["Message Center (AgentMessage log)"]
  session --> priv["private memory (owner=agent_instance_id)"]
  session --> artifacts["artifacts (on disk) + refs"]

  run["run (run_id)"] -->|belongs to| session
  run --> runEvents["run events (ExecutionEvent stream)"]
  mc -->|triggers| tasks["local task* (task_id)"]
  tasks -->|hosted by| agent["AgentInstance (agent_instance_id)"]
  tasks --> iters["iteration*"]
```

### 8.2 消息驱动执行流程（目标设计）

下面这张图强调的是目标设计：执行核心是 `AgentMessage`，`task` 只是 Agent 在本地托管的一段工作；跨 Agent 依赖通过 RPC 消息表达，不通过 child-task dependency graph 表达。外部入口（包括启动与停止）在概念上先进入 `AgentCenter`，再由 `UserProxyAgent` 决定如何进入执行链。  
这张图只表达**消息收发与生命周期边界**，不展开 Agent 内部是否使用工具、LLM、Memory 或其它执行细节。

边界约束：
- `RunManager`：只负责 `run.started / run.finished / stop`
- `AgentCenter`：只负责入口接收、消息路由、订阅匹配、Message Center 持久化
- `UserProxyAgent`：只负责外部请求 → `AgentMessage`，并决定 start/continue/stop 等控制动作
- `AssistantAgent`：只作为消息节点，接受消息并根据需要发出新消息
- `task` 是 Agent 内部托管细节，不是这张消息流程图的核心表达对象

```mermaid
flowchart TD
    HTTP["POST /runs"] --> ING["AgentCenter.ingress(external request)"]
    ING --> UPMSG["UserProxyAgent.on_message()"]
    UPMSG --> UDEC{"Start run / reject / continue?"}
    UDEC -- Reject --> UREJ["Reject request"]
    UDEC -- Start or Continue --> RM["RunManager.open / attach run lifecycle"]
    RM --> RS["RunManager.emit(run.started)"]
    RS --> UOUT["UserProxyAgent.send(target rpc request)"]

    UOUT --> AC1["AgentCenter.route(target rpc request)"]
    AC1 --> TRES{"Target object"}
    TRES -- AgentInstance --> TINST["Deliver to concrete AgentInstance"]
    TRES -- AgentProfile --> TPROF["Resolve or create matching AgentInstance"]
    TINST --> AMSG["AssistantAgent.receive(message)"]
    TPROF --> AMSG

    AMSG --> AACT{"Agent A next action"}
    AACT -- Reply --> BRSP["Agent A send(target rpc response)"]
    AACT -- Delegate --> ARPC["Agent A send(target rpc request)"]
    AACT -- Broadcast --> AEVT["Agent A send(broadcast event)"]
    AACT -- Complete local work --> ADONE["Agent A completes current local work"]

    ARPC --> AC2["AgentCenter.route(target rpc request)"]
    AC2 --> BMSG["AssistantAgent.receive(message)"]
    BMSG --> BACT{"Agent B next action"}
    BACT -- Reply --> BRSP2["Agent B send(target rpc response)"]
    BACT -- Delegate again --> BRPC["Agent B send(target rpc request)"]
    BACT -- Broadcast --> BEVT["Agent B send(broadcast event)"]
    BACT -- Complete local work --> BIDLE["Agent B idle"]

    BRPC --> AC2

    BRSP2 --> AC3["AgentCenter.route(target rpc response)"]
    BRSP --> AC3["AgentCenter.route(target rpc response)"]
    AC3 --> ANEW["Caller agent receives rpc response"]
    ANEW --> ANEXT{"Caller next action"}
    ANEXT -- Send another message --> AMSG
    ANEXT -- Stay idle --> AIDLE["Caller idle"]

    ADONE --> AFINAL{"Reply to top-level caller?"}
    AFINAL -- Yes --> ARSP["AgentBase.reply(rpc response) to UserProxyAgent"]
    ARSP --> AC4["AgentCenter.route(target rpc response)"]
    AC4 --> UPDONE["UserProxyAgent receives final response"]
    UPDONE --> RF["RunManager.emit(run.finished)"]
    RF --> OUT["Snapshot / Events / WS"]
    AFINAL -- No --> AIDLE

    AEVT --> ACB["AgentCenter.route(broadcast event)"]
    BEVT --> ACB
    EVT["Any Agent sends broadcast event(topic)"] --> ACB
    ACB --> SUB["Match AgentInstances subscribed to topic"]
    SUB --> EVREC["Subscribed agents receive(event)"]
    EVREC --> EVNEXT{"Receiver next action"}
    EVNEXT -- Send message --> AMSG
    EVNEXT -- Ignore or stay idle --> EVIDLE["Receiver idle"]

    STOP["POST /runs/{run_id}/stop"] --> SING["AgentCenter.ingress(external stop request)"]
    SING --> SUP["UserProxyAgent.on_message()"]
    SUP --> SDEC{"Stop target run?"}
    SDEC -- Yes --> RSTOP["RunManager.stop_run()"]
    SDEC -- No --> SIGN["Ignore or reject stop request"]
    RSTOP --> TCANCEL["TaskManager.stop_hosted_tasks()"]
    TCANCEL --> RFIN["RunManager.emit(run.finished: stopped)"]
``` 

---

## 9. v1 约定（便于落地）
- tool_result 默认 private（owner=当前执行 AgentInstance）
- shared 事实源统一来自 Message Center（用户输入、Agent 输出、RPC 请求/响应等都走消息中心）
- Memory 构建单 Agent 视图：仅纳入“该 Agent 可接收”的 Message Center 消息（target 命中或 broadcast topic 订阅命中）
- 摘要/压缩：v1 仅对 private 做滚动摘要；shared 只做预算丢弃/截断兜底
- shared 中必须有“请求/结果/产物引用”的高层记录，否则其它 Agent 无法协作
- artifact 内容默认不进入 prompt；只存引用，按需读取
