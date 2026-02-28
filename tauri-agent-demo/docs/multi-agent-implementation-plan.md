# Multi-Agent 系统完整实施计划（V1）

## 1. 目标冻结

1. 架构目标：平级 `Agent Instance` + 任务图编排 + 消息/文件交接，不再以 `subagent` 为核心语义。
2. 交付目标：支持“任意 Agent 接任务 -> 执行 -> handoff 给其他 Agent -> 完成任务图”。
3. 并发目标：V1 保持 `asyncio` 并发，不采用“每 Agent 一线程”；阻塞点下沉到 `to_thread/子进程`。

---

## 2. 里程碑排期（以 2026-02-28 为起点）

| 里程碑 | 时间窗口 | 交付内容 | 验收标准 |
| --- | --- | --- | --- |
| M0 规范冻结 | 2026-03-02 ~ 2026-03-06 | Task/Handoff/Artifact/Event schema、状态机、错误码 | schema 可序列化、状态迁移无歧义 |
| M1 数据层 | 2026-03-09 ~ 2026-03-13 | 新表与 DAO：`agent_instances/agent_tasks/agent_task_events/agent_artifacts/agent_task_edges` | 本地迁移成功，CRUD 可用 |
| M2 编排核心 | 2026-03-16 ~ 2026-03-27 | `TaskOrchestrator`、调度器、任务执行循环、handoff、取消、重试 | 跑通 2-agent 串联任务 |
| M3 API/WS | 2026-03-30 ~ 2026-04-03 | `/tasks` 系列接口 + `task_*` WS 事件 | API 可创建/移交/取消，WS 实时可见 |
| M4 前端 | 2026-04-06 ~ 2026-04-17 | 任务视图、任务事件流、handoff 可视化 | 前端可完整操作任务链 |
| M5 兼容层 | 2026-04-20 ~ 2026-04-24 | `spawn_subagent -> task API` 适配 | 旧入口保持可用 |
| M6 稳定性 | 2026-04-27 ~ 2026-05-08 | 限流、幂等、恢复、压测、权限收口 | 压测与故障注入通过 |
| M7 发布 | 2026-05-11 ~ 2026-05-15 | 灰度开关、迁移文档、回滚预案 | 可灰度发布并可回滚 |

---

## 3. 模块改造清单

### 3.1 后端模型

- 在 `python-backend/models.py` 新增 Task/Artifact/Event/Instance 模型。
- 旧的 `parent_session_id` 保留为兼容字段，不作为新核心关系。

### 3.2 数据库

- 在 `python-backend/database.py` 增加任务中心表与 DAO。
- 任务关系改为 `task_edges`，不再依赖文本反解析。

### 3.3 编排器

- 新增 `python-backend/task_orchestrator.py`。
- 新增 `python-backend/agent_instances.py`（实例生命周期与调度入口）。
- 现有 agent 执行流程改为“由任务驱动”。

### 3.4 API

- 在 `python-backend/main.py` 新增：
  - `POST /tasks`
  - `POST /tasks/{id}/handoff`
  - `POST /tasks/{id}/cancel`
  - `GET /tasks/{id}`
  - `GET /tasks/{id}/events`
- `POST /chat/agent/stream` 保留为兼容入口，内部映射到任务执行。

### 3.5 WS

- 在 `python-backend/ws_hub.py` 扩展事件：
  - `task_started`
  - `task_progress`
  - `task_handoff`
  - `task_completed`
  - `task_failed`
- 默认采用显式订阅策略，降低全量广播噪声。

### 3.6 兼容层

- `spawn_subagent` 逐步退化为任务接口适配器。
- 兼容期内保留 `subagent_*` 事件，但内部来源应是任务状态。

### 3.7 前端

- `src/types.ts`：新增任务相关类型定义。
- `src/api.ts`：新增 `/tasks` 客户端 API。
- `src/App.tsx`：新增任务面板、事件流、handoff 展示。

---

## 4. 测试与验收计划

1. 单元测试：状态机迁移、重试策略、取消传播、幂等校验。
2. 集成测试：`coding -> test -> reviewer` 三段式 handoff。
3. E2E：前端创建任务、订阅事件、取消任务、断线重连恢复。
4. 故障注入：worker 崩溃、WS 断连、DB 锁冲突、重复提交。
5. 发布门槛：关键链路通过率 >= 99%，无 P0/P1。

---

## 5. 风险与控制

1. 旧会话语义与新任务语义冲突。  
控制：feature flag 双轨运行 + 兼容 API。

2. 并发任务导致路径越权。  
控制：统一路径校验与权限策略（`work_path + extra_work_paths`）。

3. WS 事件风暴。  
控制：显式订阅 + 事件节流 + 历史事件分页拉取。

4. 进程重启导致任务丢失。  
控制：任务持久化 + 启动恢复扫描。

---

## 6. 执行优先级（建议）

1. 先完成 M0（协议、状态机、API 合同）。
2. 再完成 M1（数据库迁移与 DAO）。
3. 再做 M2 最小闭环（创建任务 -> 执行 -> handoff -> 完成）。
4. 最后逐步替换旧 `subagent` 语义路径。

---

## 7. 当前结论

实施顺序应为“协议与任务中心先行，线程模型后置优化”。  
优先把系统从“会话树 + subagent”迁移为“任务图 + 平级 agent 实例”。
