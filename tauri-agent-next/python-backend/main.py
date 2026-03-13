from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app_logging import log_error, log_info
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
        log_info("app.startup.begin", data_dir=str(resolved_services.data_dir))
        await resolved_services.startup()
        try:
            log_info("app.startup.ready", data_dir=str(resolved_services.data_dir))
            yield
        finally:
            log_info("app.shutdown.begin", data_dir=str(resolved_services.data_dir))
            await resolved_services.shutdown()
            log_info("app.shutdown.complete", data_dir=str(resolved_services.data_dir))

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
    log_info("server.run.begin", host=host, port=port)
    try:
        uvicorn.run("main:app", host=host, port=port, reload=False)
    except Exception as exc:
        log_error("server.run.failed", host=host, port=port, error=str(exc))
        raise


if __name__ == "__main__":
    main()
