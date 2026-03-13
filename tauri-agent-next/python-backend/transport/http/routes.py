from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app_config import get_app_config, get_app_config_path
from models import (
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    SessionPrivateFactsResponse,
    SessionSharedFactsResponse,
    StopRunResponse,
)
from observation.facts import ObservationScope
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


@router.get(
    "/sessions/{session_id}/facts/shared",
    response_model=SessionSharedFactsResponse,
)
async def list_session_shared_facts(
    request: Request,
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    run_id: str | None = None,
) -> SessionSharedFactsResponse:
    services = request.app.state.services
    scope = ObservationScope(session_id=session_id, run_id=run_id)
    shared_facts = await services.fact_query_service.list_shared(
        scope,
        after_seq=after_seq,
        limit=limit,
    )
    next_after_seq = shared_facts[-1].fact_seq if shared_facts else after_seq
    return SessionSharedFactsResponse(
        session_id=session_id,
        shared_facts=shared_facts,
        next_after_seq=next_after_seq,
    )


@router.get(
    "/sessions/{session_id}/facts/private",
    response_model=SessionPrivateFactsResponse,
)
async def list_session_private_facts(
    request: Request,
    session_id: str,
    agent_id: str = Query(..., min_length=1),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    run_id: str | None = None,
) -> SessionPrivateFactsResponse:
    services = request.app.state.services
    scope = ObservationScope(
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        include_private=True,
    )
    private_events = await services.fact_query_service.list_private(
        scope,
        after_id=after_id,
        limit=limit,
    )
    next_after_id = private_events[-1].private_event_id if private_events else after_id
    return SessionPrivateFactsResponse(
        session_id=session_id,
        agent_id=agent_id,
        private_events=private_events,
        next_after_id=next_after_id,
    )
