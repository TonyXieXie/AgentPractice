import argparse

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from runtime_paths import get_database_path
from .lifecycle import on_startup, shutdown_cleanup
from .routers.base import router as base_router
from .routers.chat import router as chat_router
from .routers.configs import router as configs_router
from .routers.pty import router as pty_router
from .routers.sessions import router as sessions_router
from .routers.tools import router as tools_router
from tools.base import ToolRegistry
from tools.builtin import register_builtin_tools



def create_app() -> FastAPI:
    app = FastAPI(title="Tauri Agent Chat Backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ToolRegistry.clear()
    register_builtin_tools()

    app.add_event_handler("startup", on_startup)
    app.add_event_handler("shutdown", shutdown_cleanup)

    app.include_router(base_router)
    app.include_router(configs_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(pty_router)
    app.include_router(tools_router)
    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tauri Agent Backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print("Starting FastAPI server...")
    print("Supported LLMs: OpenAI, ZhipuAI, Deepseek")
    print(f"Database: SQLite ({get_database_path()})")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
