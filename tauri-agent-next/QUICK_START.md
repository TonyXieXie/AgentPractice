# Quick Start

## 1. Requirements

- Python 3.12

## 2. Install dependencies

```powershell
cd python-backend
python -m pip install -r requirements.txt
```

## 3. Start the backend

```powershell
cd python-backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## 4. Smoke checks

- HTTP health: `GET http://127.0.0.1:8000/healthz`
- Read merged config: `GET http://127.0.0.1:8000/config`
- WS gateway: connect `ws://127.0.0.1:8000/ws`
- Emit one debug chunk: `POST http://127.0.0.1:8000/debug/emit`

## 5. Run tests

```powershell
cd python-backend
python -m pytest tests
```
