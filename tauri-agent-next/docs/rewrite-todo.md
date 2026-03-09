# tauri-agent-next 重写执行 TODO

- 状态：In Progress
- 日期：2026-03-09
- 目标：在 `tauri-agent-next` 中重建新的 Agent 协作、观测与 `HTTP + WS` 实时通信架构
- 原则：复用低耦合基础设施，重写高耦合执行链路，不继续沿用 `subagent + session-centric + SSE` 模型

## 1. 重写边界

### 1.1 直接复用或复制的内容

优先复制下面这些低耦合基础设施，再在新目录内按新架构改造：

- `tauri-agent-demo/python-backend/llm_client.py`
- `tauri-agent-demo/python-backend/tools/base.py`
- `tauri-agent-demo/python-backend/tools/context.py`
- `tauri-agent-demo/python-backend/ws_hub.py`
- 纯工具实现中与 Agent 协作无关的部分

### 1.2 不直接复用的内容

下面这些内容不要直接搬进新主链路：

- `tauri-agent-demo/python-backend/server/chat_agent_runtime.py`
- `tauri-agent-demo/python-backend/subagent_runner.py`
- `tauri-agent-demo/python-backend/tools/builtin/subagent_tool.py`
- `tauri-agent-demo/python-backend/agents/react.py`
- `tauri-agent-demo/src/shared/api/internal/streaming.ts`
- 旧 `subagent_profile`、`spawnable`、`parent_session_id` 协作语义

### 1.3 本期最小目标

第一阶段不要追求全量替换，只要完成：

- 一个新的 `AgentBase`
- 一个新的 `AssistantAgent`
- 一个新的 `UserProxyAgent`
- 一个新的 `AgentCenter`
- 一个新的 `ExecutionObserver / ObservationCenter`
- 一条 `HTTP + WS` 最小闭环
- 一次真实的工具调用和一段可展示的 chunk 流

## 2. 目录计划

建议先按下面的结构推进：

- `python-backend/agents/`
- `python-backend/agents/execution/`
- `python-backend/observation/`
- `python-backend/transport/http/`
- `python-backend/transport/ws/`
- `python-backend/tools/`
- `python-backend/llm/`
- `python-backend/repositories/`
- `src/`
- `docs/`

## 3. Phase 0：项目骨架与基线

### TODO

- [x] 在 `tauri-agent-next` 下初始化独立 backend 入口
- [x] 明确新项目的配置加载方式
- [ ] 补一个最小 `README.md`
- [ ] 补一个最小启动说明
- [x] 约定新项目不反向 import 旧项目运行时主链路

### 验收标准

- [x] 新目录能独立启动一个空服务
- [x] 新项目的 import 路径不依赖旧项目运行时入口

## 4. Phase 1：复制低耦合基础设施

### TODO

- [x] 复制 `LLMClient` 到 `python-backend/llm/client.py`
- [x] 复制 `Tool` / `ToolRegistry` 到 `python-backend/tools/base.py`
- [x] 复制 `tool context` 到 `python-backend/tools/context.py`
- [x] 复制 `ws_hub.py` 到 `python-backend/transport/ws/ws_hub.py`
- [x] 清理这些模块对旧路径的 import 依赖
- [x] 给复制过来的模块补一个最小 smoke test 计划

### 迁移注意

- `tool context` 不要继续只围绕 `session_id`
- 新版 context 至少要预留：
  - `agent_id`
  - `run_id`
  - `message_id`
  - `tool_call_id`
  - `work_path`

### 验收标准

- [x] `LLMClient` 能在新项目中独立 import
- [x] `Tool` 基类与注册表可正常工作
- [x] `WS Hub` 能跑通最小连接与广播

## 5. Phase 2：定义核心协议与数据结构

### 需要创建的文件

- [x] `python-backend/agents/message.py`
- [x] `python-backend/agents/instance.py`
- [x] `python-backend/observation/events.py`
- [x] `python-backend/transport/ws/ws_types.py`

### TODO

- [x] 定义 `AgentMessage`
- [x] 定义 `AgentInstance`
- [x] 定义 `ExecutionEvent`
- [x] 定义 `ExecutionSnapshot`
- [x] 定义 `WSChunk`
- [x] 明确 `rpc_request / rpc_response / event` 三类消息
- [x] 明确 `unicast / broadcast` 两类投递方式
- [x] 明确 `run_id / agent_id / seq / visibility / level` 字段

### 验收标准

- [x] 所有核心协议都有稳定 dataclass / pydantic 定义
- [x] 字段命名不再依赖旧 SSE payload 结构
- [x] `WSChunk` 可以覆盖 run、agent、tool、llm 四类实时流

## 6. Phase 3：建立新的 Agent 协作模型

### 需要创建的文件

- [x] `python-backend/agents/base.py`
- [x] `python-backend/agents/assistant.py`
- [x] `python-backend/agents/user_proxy.py`
- [x] `python-backend/agents/center.py`

### TODO

- [x] 实现 `AgentBase`
- [x] 在 `AgentBase` 中封装收发消息能力
- [x] 在 `AgentBase` 中封装 observer 上报入口
- [x] 实现 `AssistantAgent`
- [x] 实现 `UserProxyAgent`
- [x] 实现 `AgentCenter` 的注册、查找、路由能力
- [x] 支持最小的点对点 RPC
- [x] 支持最小的单播 Event
- [x] 预留广播 Event 接口

### 验收标准

- [x] `UserProxyAgent` 能向 `AssistantAgent` 发消息
- [x] `AgentCenter` 能路由并返回一次最小 RPC 响应
- [x] 不再依赖 `subagent` 或 child session 实现协作

## 7. Phase 4：建立新的执行内核壳层

### 需要创建的文件

- [x] `python-backend/agents/execution/engine.py`
- [x] `python-backend/agents/execution/strategy.py`
- [x] `python-backend/agents/execution/simple_strategy.py`
- [x] `python-backend/agents/execution/react_strategy.py`
- [x] `python-backend/agents/execution/tool_executor.py`
- [x] `python-backend/agents/execution/context_builder.py`
- [x] `python-backend/agents/execution/step_emitter.py`

### TODO

- [x] 把执行内核从协作模型中分离出来
- [x] 先实现 `SimpleStrategy`
- [x] 给 `AssistantAgent` 接上 `ExecutionEngine`
- [x] 通过 adapter 临时复用旧工具调用能力
- [x] 逐步拆分旧 `react.py` 的能力，而不是整体复制
- [x] 明确 tool 执行与 llm 执行的边界

### 验收标准

- [x] `AssistantAgent` 能跑一次最小执行
- [x] 一次执行能产出标准化 `ExecutionEvent`
- [x] 新执行引擎可以不依赖旧 `chat_agent_runtime`

## 8. Phase 5：建立统一观测层

### 需要创建的文件

- [x] `python-backend/observation/observer.py`
- [x] `python-backend/observation/center.py`
- [x] `python-backend/observation/router.py`
- [x] `python-backend/observation/snapshot_builder.py`
- [x] `python-backend/observation/projection_builder.py`

### TODO

- [x] 实现统一的 `ExecutionObserver.emit(...)`
- [x] 实现 `ObservationCenter`
- [x] 实现按范围订阅的 `SubscriptionRouter`
- [x] 支持按 `run_id` 过滤
- [x] 支持按 `agent_id` 过滤
- [x] 支持 `visibility / level` 过滤
- [x] 建立 `RunProjection / AgentProjection / ToolCallProjection`

### 验收标准

- [x] 可以只看 `AgentA`，不看 `AgentB`
- [x] tool 调用过程可被前端单独观测
- [x] 历史快照与实时事件语义一致

## 9. Phase 6：建立 `HTTP + WS` 传输层

### 需要创建的文件

- [x] `python-backend/transport/http/routes.py`
- [x] `python-backend/transport/ws/gateway.py`
- [ ] `python-backend/transport/ws/session.py`
- [ ] `src/lib/wsClient.ts`
- [ ] `src/lib/wsTypes.ts`

### HTTP TODO

- [x] 提供创建 run 的接口
- [x] 提供停止 run 的接口
- [x] 提供查询 snapshot 的接口
- [x] 提供查询历史事件的接口

### WS TODO

- [x] 支持 `subscribe(scope)`
- [x] 支持 `unsubscribe(scope)`
- [x] 支持 `set_scope(scope)`
- [x] 支持 `resume(after_seq)`
- [x] 支持 `heartbeat`
- [x] 支持 `chunk / ack / error` 三类基础返回

### 验收标准

- [x] 前端能通过 HTTP 创建一次 run
- [x] 前端能通过 WS 订阅到 run 级事件
- [x] 前端能切换到单 Agent 视角
- [x] 前端能接收到 tool / llm chunk

## 10. Phase 7：持久化与历史回放

### 需要创建的文件

- [x] `python-backend/repositories/event_repository.py`
- [ ] `python-backend/repositories/run_repository.py`
- [ ] `python-backend/repositories/agent_repository.py`

### TODO

- [x] 建立 `EventStore`
- [x] 支持 append-only 事件写入
- [x] 支持按 `run_id + seq` 查询
- [x] 支持 `after_seq` 补齐
- [x] 支持 snapshot 落盘或缓存
- [x] 支持最小历史回放

### 验收标准

- [x] 实时断线后可以从 `after_seq` 恢复
- [x] 历史时间线可以分页查看
- [x] 快照与历史事件可以相互印证

## 11. Phase 8：前端最小观察界面

### TODO

- [ ] 做一个任务总览页
- [ ] 做一个 Agent 详情页
- [ ] 做一个 tool 调用详情区域
- [ ] 支持按 `run_id` 和 `agent_id` 切换观察范围
- [ ] 支持显示 chunk 级流式内容

### 验收标准

- [ ] 能看整体任务执行情况
- [ ] 能看单个 Agent 的状态与消息
- [ ] 能看 React Agent 的工具调用过程

## 12. Phase 9：迁移与旧系统切换

### TODO

- [ ] 确认哪些 tool 要从旧项目复制到新项目
- [ ] 确认是否需要迁移旧 session/message 数据
- [ ] 明确新旧系统并行期的入口策略
- [ ] 最后再删除旧 `subagent` 相关实现

### 验收标准

- [x] 新系统可在不依赖旧 runtime 的情况下独立运行
- [x] 旧系统只保留参考价值，不再作为新架构演进基础

## 13. 第一周建议执行顺序

建议按下面顺序推进，先拿到最小闭环：

- [x] 建 `python-backend/llm/client.py`
- [x] 建 `python-backend/tools/base.py`
- [x] 建 `python-backend/tools/context.py`
- [x] 建 `python-backend/agents/message.py`
- [x] 建 `python-backend/agents/base.py`
- [x] 建 `python-backend/agents/assistant.py`
- [x] 建 `python-backend/agents/user_proxy.py`
- [x] 建 `python-backend/agents/center.py`
- [x] 建 `python-backend/observation/events.py`
- [x] 建 `python-backend/observation/observer.py`
- [x] 建 `python-backend/transport/ws/ws_types.py`
- [x] 建 `python-backend/transport/ws/gateway.py`
- [x] 建一个最小 `HTTP create-run` 接口
- [x] 建一个最小 `WS subscribe` 接口
- [x] 跑通第一次 end-to-end demo

## 14. 当前不做的事情

为了避免项目第一阶段失焦，下面这些先不做：

- [x] 不做多 Agent 复杂广播编排
- [x] 不做完整的历史数据迁移
- [x] 不直接兼容旧 SSE 协议
- [x] 不保留 `subagent` 兼容层
- [x] 不把旧 `react.py` 整体复制过来继续维护

## 15. Done 定义

当下面这些条件同时满足时，可以认为新项目完成了第一阶段：

- [x] `HTTP + WS` 最小链路稳定
- [x] 一个 `UserProxyAgent` 和一个 `AssistantAgent` 能完成一次真实任务
- [x] tool 调用过程可观测
- [x] 可以按 `run_id / agent_id` 过滤前端观察范围
- [x] 能查看一次历史执行过程
- [x] 新项目主链路不依赖 `subagent`
