# tauri-agent-next

新的 Agent 协作后端与 Tauri/Vite 前端工作台。

当前实现已经切到事实模型：

- 后端写入源只有 `shared_facts` 和 `agent_private_events`
- `ObservationCenter` 是唯一写入与查询入口
- 前端只消费原始 facts，并在本地组装 handoff、agent trace、run summary
- WebSocket 只负责 raw fact append 和断线续传，不再提供 snapshot/projection 兼容层

## 当前目录

- `python-backend/`
- `src/`
- `src-tauri/`
- `docs/`
- `docs/ws-session-observation-private-design.md`

## 启动方式

```powershell
cd python-backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Windows 也可以直接运行 `StartBackend.bat [port]`。

前后端联调可以运行：

```text
StartDev.bat
StartDev.bat --no-browser
```

这条链路会动态选择空闲端口并启动：

- FastAPI backend
- Vite frontend

也就是说，`StartDev.bat` 默认是浏览器联调入口，不是桌面应用窗口。

如果要直接以桌面应用方式启动，请运行：

```text
StartDesktop.bat
```

这条链路会启动：

- FastAPI backend
- Tauri desktop app

实际端口会写入 `.tauri-agent-next-data/last-dev-ports.json`。

## 当前 HTTP / WS 合同

- `GET /healthz`
- `GET /config`
- `GET /observe`
- `POST /runs`
- `POST /runs/{run_id}/stop`
- `GET /sessions/{session_id}/facts/shared`
- `GET /sessions/{session_id}/facts/private?agent_id=...`
- `WS /ws`

WebSocket 当前协议：

- inbound: `set_scope`, `request_bootstrap`, `resume_shared`, `resume_private`, `heartbeat`
- outbound: `ack`, `error`, `bootstrap.shared_facts`, `bootstrap.private_events`, `bootstrap.cursors`, `append.shared_fact`, `append.private_event`

前端当前使用方式：

- 初始加载走 HTTP facts 查询
- 实时更新和断线续传走 WS
- URL / 分享入口以 `session_id` 为主，`run_id` 只作为可选过滤条件

## 桌面应用说明

- `src-tauri/` 已经是 Tauri 桌面壳，不是纯 Web 前端。
- 当前仓库可以直接以开发态桌面应用运行。
- 当前打包配置还是关闭状态；`src-tauri/tauri.conf.json` 里 `bundle.active` 现在是 `false`，所以默认不是“可安装 exe 打包”流程。

## 观察页

启动 backend 后，可以打开：

```text
http://127.0.0.1:8000/observe
```

前端会按 session scope 加载 shared/private facts，并在本地重建多 Agent 视图。

## 验证

后端测试：

```powershell
cd python-backend
python -m unittest discover -s tests
```

前端构建：

```powershell
npm run build
```
