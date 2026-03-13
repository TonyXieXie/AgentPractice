# Quick Start

## 1. Requirements

- Python 3.12
- Node.js 20+

## 2. Install backend dependencies

```powershell
cd python-backend
python -m pip install -r requirements.txt
```

## 3. Install frontend dependencies

```powershell
npm install
```

## 4. Start the backend

```powershell
cd python-backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Windows 也可以直接运行：

```text
StartBackend.bat
StartBackend.bat 8000
```

## 5. Start frontend + backend together

```text
StartDev.bat
StartDev.bat --no-browser
```

脚本会自动选择空闲端口并把结果写入 `.tauri-agent-next-data/last-dev-ports.json`。

## 6. Smoke checks

- HTTP health: `GET http://127.0.0.1:8000/healthz`
- Read merged config: `GET http://127.0.0.1:8000/config`
- Open observation page: `http://127.0.0.1:8000/observe`
- Create one run: `POST http://127.0.0.1:8000/runs`
- Query shared facts: `GET http://127.0.0.1:8000/sessions/{session_id}/facts/shared`
- Query private facts: `GET http://127.0.0.1:8000/sessions/{session_id}/facts/private?agent_id=...`
- WS gateway: connect `ws://127.0.0.1:8000/ws`

当前事实流协议：

- HTTP 只查询 raw facts
- WS 只推 `append.shared_fact` / `append.private_event` 和 bootstrap/resume 相关控制帧
- 不再提供旧的 run 快照/事件查询接口

## 7. Run checks

后端测试：

```powershell
cd python-backend
python -m unittest discover -s tests
```

前端构建：

```powershell
npm run build
```
