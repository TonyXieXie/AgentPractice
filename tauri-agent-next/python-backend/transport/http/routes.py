from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app_config import get_app_config, get_app_config_path
from models import (
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    ListRunEventsResponse,
    RunSnapshotResponse,
    StopRunResponse,
)
from runtime_paths import ensure_runtime_dirs
from run_manager import SessionBusyError, SessionNotFoundError


router = APIRouter()
OBSERVE_PAGE = Path(__file__).resolve().parents[2] / "static" / "observe" / "index.html"


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


@router.get("/observe")
async def observe_page():
    return FileResponse(OBSERVE_PAGE)


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(request: Request, body: CreateRunRequest) -> CreateRunResponse:
    services = request.app.state.services
    try:
        return await services.agent_center.ingress_run_request(body)
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "busy",
                "error": str(exc),
                "session_id": exc.session_id,
                "run_id": exc.active_run_id,
            },
        ) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/stop", response_model=StopRunResponse)
async def stop_run(request: Request, run_id: str) -> StopRunResponse:
    services = request.app.state.services
    response = await services.agent_center.ingress_stop_request(run_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return response


@router.get("/runs/{run_id}/snapshot", response_model=RunSnapshotResponse)
async def get_run_snapshot(request: Request, run_id: str) -> RunSnapshotResponse:
    services = request.app.state.services
    snapshot = await services.observation_center.get_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    projections = await services.observation_center.get_projection_state(run_id)
    return RunSnapshotResponse(
        run_id=run_id,
        snapshot=snapshot,
        run_projection=projections.run_projection,
        agent_projections=projections.agent_projections,
        tool_call_projections=projections.tool_call_projections,
    )


@router.get("/runs/{run_id}/events", response_model=ListRunEventsResponse)
async def list_run_events(
    request: Request,
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    agent_id: str | None = None,
    visibility: str | None = None,
    level: str | None = None,
) -> ListRunEventsResponse:
    services = request.app.state.services
    snapshot = await services.observation_center.get_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    events = await services.observation_center.list_events(
        run_id,
        after_seq=after_seq,
        limit=limit,
        agent_id=agent_id,
        visibility=visibility,
        level=level,
    )
    next_after_seq = after_seq
    if events:
        next_after_seq = int(events[-1].seq or after_seq)
    return ListRunEventsResponse(
        run_id=run_id,
        events=events,
        next_after_seq=next_after_seq,
    )
