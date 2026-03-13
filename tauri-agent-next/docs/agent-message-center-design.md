# Agent Message Center Design

- 状态：Current
- 日期：2026-03-13

这份文档不再描述旧的快照投影协议与旧实时分块信封。
当前实现已经落到下面这组边界：

## 核心结论

- Agent 间协作仍通过 `AgentMessage` 和 `AgentCenter` 完成。
- 进入系统后的共享协作结果统一持久化为 `shared_facts`。
- 每个 Agent 的私有执行过程统一持久化为 `agent_private_events`。
- `ObservationCenter` 是 shared/private 两条事实流的唯一写入与查询入口。
- 前端不再依赖后端 projection；它只接收 raw facts，并在本地组装总览、handoff 和 agent trace。

## 当前事实模型

### Shared Fact

- 字段：`fact_id`, `session_id`, `run_id`, `fact_seq`, `message_id`, `sender_id`, `target_agent_id`, `target_profile_id`, `topic`, `fact_type`, `payload_json`, `metadata_json`, `visibility`, `level`, `created_at`
- 语义：用户输入、Agent handoff、RPC request/response、event handoff、run lifecycle 都进入 shared facts

### Private Execution Event

- 字段：`private_event_id`, `session_id`, `owner_agent_id`, `run_id`, `task_id`, `message_id`, `tool_call_id`, `trigger_fact_id`, `parent_private_event_id`, `kind`, `payload_json`, `created_at`
- 语义：`tool_call`, `tool_result`, `reasoning_note`, `reasoning_summary`, `private_summary`, `execution_error`

## 当前 HTTP / WS 面

### HTTP

- `POST /runs`
- `POST /runs/{run_id}/stop`
- `GET /sessions/{session_id}/facts/shared`
- `GET /sessions/{session_id}/facts/private?agent_id=...`

### WebSocket

- inbound: `set_scope`, `request_bootstrap`, `resume_shared`, `resume_private`, `heartbeat`
- outbound: `ack`, `error`, `bootstrap.shared_facts`, `bootstrap.private_events`, `bootstrap.cursors`, `append.shared_fact`, `append.private_event`

`set_scope` 当前字段：

- `target_session_id`
- `selected_run_id`
- `selected_agent_id`
- `include_private`

## 前端对齐原则

- URL / 分享入口以 `session_id` 为主。
- 初始加载使用 HTTP facts 查询。
- 实时更新和断线续传使用 WS append/resume。
- handoff feed 与 graph 基于 shared facts 组装。
- 单 Agent 执行视图基于“相关 shared facts + 该 Agent private events”按时间因果顺序组装。

更完整的 Observation / WS Session 设计说明见：

- `docs/ws-session-observation-private-design.md`
