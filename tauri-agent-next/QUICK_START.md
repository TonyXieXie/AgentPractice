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
- Open observation page: `http://127.0.0.1:8000/observe`
- Create one run: `POST http://127.0.0.1:8000/runs`
- Query snapshot: `GET http://127.0.0.1:8000/runs/{run_id}/snapshot`
- WS gateway: connect `ws://127.0.0.1:8000/ws`

## 5. Run tests

```powershell
cd python-backend
python -m unittest discover -s tests
```
