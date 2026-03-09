# tauri-agent-next

新的 Agent 协作后端重写目录。

当前已落下第一批骨架：

- 独立的 `python-backend` 入口
- 最小配置加载
- 独立的 `LLMClient`
- 新版 `Tool` / `ToolRegistry` / `ToolContext`
- 基于 `run_id` / `agent_id` 的最小 `WS Hub`
- 最小 HTTP + WS 调试链路
- smoke tests

原则：
- 保留 `tauri-agent-demo` 作为参考实现
- 在这里重建新的 Agent / Observation / HTTP + WS 架构
- 优先复制低耦合基础设施，避免长期跨目录直接依赖旧代码

## 当前目录

- `python-backend/`
- `docs/`
- `src/`：前端观察界面占位，暂未启动

## 启动方式

```powershell
cd python-backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Windows 也可以直接运行 `StartBackend.bat`。

## 可用接口

- `GET /healthz`
- `GET /config`
- `POST /debug/emit`
- `WS /ws`

## 验证

```powershell
cd python-backend
python -m pytest tests
```

执行清单见：`docs/rewrite-todo.md`
