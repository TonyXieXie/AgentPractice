from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app_config import get_app_config
from app_services import AppServices, build_app_services
from transport.http.routes import router as http_router
from transport.ws.gateway import router as ws_router


def create_app(*, services: AppServices | None = None) -> FastAPI:
    resolved_services = services or build_app_services()
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = resolved_services
        await resolved_services.startup()
        try:
            yield
        finally:
            await resolved_services.shutdown()

    app = FastAPI(
        title="tauri-agent-next backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(http_router)
    app.include_router(ws_router)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.services = resolved_services

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
