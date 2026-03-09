from __future__ import annotations

from fastapi import APIRouter

from app_config import get_app_config, get_app_config_path
from models import DebugEmitRequest, HealthResponse
from runtime_paths import ensure_runtime_dirs
from transport.ws.ws_hub import get_ws_hub


router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        config_path=get_app_config_path(),
        runtime_data_dir=str(ensure_runtime_dirs()),
    )


@router.get("/config")
async def read_config():
    return {"config": get_app_config()}


@router.post("/debug/emit")
async def debug_emit(request: DebugEmitRequest):
    seq = await get_ws_hub().emit(
        stream=request.stream,
        payload={**request.payload, "topic": request.topic},
        run_id=request.run_id,
        agent_id=request.agent_id,
        done=request.done,
    )
    return {"ok": True, "seq": seq}
