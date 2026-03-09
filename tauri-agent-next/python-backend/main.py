from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI

from app_config import get_app_config
from runtime_paths import ensure_runtime_dirs
from transport.http.routes import router as http_router
from transport.ws.gateway import router as ws_router
from transport.ws.ws_hub import get_ws_hub


def create_app() -> FastAPI:
    app = FastAPI(title="tauri-agent-next backend", version="0.1.0")
    app.include_router(http_router)
    app.include_router(ws_router)

    @app.on_event("startup")
    async def _startup() -> None:
        ensure_runtime_dirs()
        get_ws_hub().set_loop(asyncio.get_running_loop())

    return app


app = create_app()


def main() -> None:
    http_config = get_app_config().get("transport", {}).get("http", {})
    host = str(http_config.get("host", "127.0.0.1"))
    try:
        port = int(http_config.get("port", 8000))
    except (TypeError, ValueError):
        port = 8000
    uvicorn.run("main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
