# Tauri Agent Demo

桌面端 AI Agent 工作台，技术栈为 `Tauri + React + TypeScript + FastAPI + SQLite`。

## 目录结构
- `src/`：React 前端，`App.tsx` 只保留应用壳层入口，API 通过 `src/shared/api/` 分域暴露。
- `python-backend/`：FastAPI 后端，入口收敛到 `main.py`，应用装配在 `server/app.py`。
- `python-backend/server/routers/`：按 `base/config/sessions/chat/pty/tools` 分组路由。
- `python-backend/server/services/`：配置、会话、权限等服务层。
- `repositories/`：对 `database.py` 的领域访问封装。
- `src-tauri/`：Tauri 壳层与桌面端后端拉起逻辑。

## 运行时约定
- 运行时数据统一落到 `TAURI_AGENT_DATA_DIR`。
- 未显式设置时，开发环境默认使用项目根下的 `tauri-agent-demo/.tauri-agent-data/`。
- 数据库、应用配置、工具配置、AST 设置都从该目录读取，不再从仓库根目录读取运行态文件。

## 推荐启动方式
- Windows：运行 `Run.bat`
- macOS：运行 `run.command`
- 只启动后端：`StartBackend.bat` / `start-backend.command`
- 只启动桌面端：`StartFrontend.bat` / `start-frontend.command`

## 开发脚本
- `npm run dev:backend`：启动 FastAPI（`127.0.0.1:8000`）
- `npm run dev:desktop`：连接外部后端启动 Tauri
- `npm run dev:all`：并行启动后端和桌面端
- `npm run build`：构建前端
