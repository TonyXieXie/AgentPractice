# Quick Start

## 1. 安装依赖
- Node.js 20+
- Rust / Cargo
- Python 3.12

## 2. 初始化环境
- Windows：首次运行 `StartBackend.bat`，脚本会自动创建 `python-backend/venv`
- macOS：先执行 `setup-mac.command`

## 3. 启动应用
- 整体启动：`Run.bat` 或 `run.command`
- 后端单独启动：`StartBackend.bat` 或 `start-backend.command`
- 桌面端单独启动：`StartFrontend.bat` 或 `start-frontend.command`

## 4. 运行时文件位置
- 默认目录：`tauri-agent-demo/.tauri-agent-data/`
- 包含：`chat_app.db`、`app_config.json`、`tools_config.json`、`ast_settings.json`

## 5. 开发调试
- 后端地址固定为 `http://127.0.0.1:8000`
- Tauri 开发模式默认连接外部后端，不再通过 `backend_port.txt` 协调端口
