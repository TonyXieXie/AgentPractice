# tauri-agent-next Rewrite Status

- 状态：In Progress
- 日期：2026-03-13

## 已完成

- Agent 协作主链路已切到 `AgentMessage` + `AgentCenter`
- Observation 写入已切到 `ObservationCenter`
- 事实源已统一为：
  - `shared_facts`
  - `agent_private_events`
- HTTP 查询已切到 session-scoped raw facts
- WebSocket 已切到 session-scoped raw fact streaming
- 前端工作台已切到：
  - `session_id` 驱动加载与分享
  - raw facts HTTP bootstrap
  - WS append/resume
  - 本地组装 handoff graph / conversation / agent trace

## 当前合同

### HTTP

- `POST /runs`
- `POST /runs/{run_id}/stop`
- `GET /sessions/{session_id}/facts/shared`
- `GET /sessions/{session_id}/facts/private?agent_id=...`

### WS

- inbound: `set_scope`, `request_bootstrap`, `resume_shared`, `resume_private`, `heartbeat`
- outbound: `ack`, `error`, `bootstrap.shared_facts`, `bootstrap.private_events`, `bootstrap.cursors`, `append.shared_fact`, `append.private_event`

## 当前剩余项

- 补更多前端交互验证和 e2e smoke cases
- 继续清理仓库内与旧 snapshot/projection 合同无关但仍残留的历史注释或草稿
- 根据实际使用情况，再决定是否补 session/run 浏览入口或更细的前端筛选

## 不再保留的旧合同

- 旧的 run 快照查询接口
- 旧的 run 事件分页接口
- 旧的后端 projection 类型族
- 旧的统一实时分块信封
- 旧的单游标续传语义
