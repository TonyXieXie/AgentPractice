from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app_logging import (
    LOG_CATEGORY_FRONTEND_BACKEND,
    get_log_config,
    log_debug,
    log_info,
    log_warning,
    set_log_config,
)
from app_config import get_app_config, get_app_config_path
from models import (
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    LogConfigRequest,
    LogConfigResponse,
    PromptTraceSnapshot,
    SessionPrivateFactsResponse,
    SessionPromptTraceResponse,
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


@router.get("/log", response_model=LogConfigResponse)
async def read_log_config() -> LogConfigResponse:
    return LogConfigResponse(**get_log_config())


@router.post("/log", response_model=LogConfigResponse)
async def update_log_config(body: LogConfigRequest) -> LogConfigResponse:
    config = set_log_config(
        backend_logic=body.backend_logic,
        frontend_backend=body.frontend_backend,
    )
    log_info(
        "http.log.updated",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        backend_logic=config["backend_logic"],
        frontend_backend=config["frontend_backend"],
    )
    return LogConfigResponse(**config)


@router.get("/observe")
async def observe_page():
    return FileResponse(OBSERVE_PAGE)


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(request: Request, body: CreateRunRequest) -> CreateRunResponse:
    services = request.app.state.services
    log_info(
        "http.create_run.received",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        session_id=body.session_id,
        strategy=body.strategy,
        work_path=body.work_path,
        has_request_overrides=bool(body.request_overrides),
        content_chars=len(body.content or ""),
    )
    try:
        response = await services.agent_center.ingress_run_request(body)
        log_info(
            "http.create_run.accepted",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            session_id=response.session_id,
            run_id=response.run_id,
            user_agent_id=response.user_agent_id,
            assistant_agent_id=response.assistant_agent_id,
        )
        return response
    except SessionBusyError as exc:
        log_warning(
            "http.create_run.busy",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            session_id=exc.session_id,
            active_run_id=exc.active_run_id,
            error=str(exc),
        )
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
        log_warning(
            "http.create_run.session_not_found",
            category=LOG_CATEGORY_FRONTEND_BACKEND,
            session_id=exc.session_id,
            error=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/stop", response_model=StopRunResponse)
async def stop_run(request: Request, run_id: str) -> StopRunResponse:
    services = request.app.state.services
    log_info("http.stop_run.received", category=LOG_CATEGORY_FRONTEND_BACKEND, run_id=run_id)
    response = await services.agent_center.ingress_stop_request(run_id)
    if response is None:
        log_warning("http.stop_run.not_found", category=LOG_CATEGORY_FRONTEND_BACKEND, run_id=run_id)
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    log_info(
        "http.stop_run.accepted",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        run_id=response.run_id,
        status=response.status,
    )
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
    log_debug(
        "http.list_shared_facts.completed",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        session_id=session_id,
        run_id=run_id,
        after_seq=after_seq,
        next_after_seq=next_after_seq,
        count=len(shared_facts),
    )
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
    log_debug(
        "http.list_private_events.completed",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        session_id=session_id,
        agent_id=agent_id,
        run_id=run_id,
        after_id=after_id,
        next_after_id=next_after_id,
        count=len(private_events),
    )
    return SessionPrivateFactsResponse(
        session_id=session_id,
        agent_id=agent_id,
        private_events=private_events,
        next_after_id=next_after_id,
    )


@router.get(
    "/sessions/{session_id}/prompt-trace/latest",
    response_model=SessionPromptTraceResponse,
)
async def get_latest_session_prompt_trace(
    request: Request,
    session_id: str,
    agent_id: str = Query(..., min_length=1),
    run_id: str | None = None,
) -> SessionPromptTraceResponse:
    services = request.app.state.services
    scope = ObservationScope(
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        include_private=True,
    )
    trace = await services.fact_query_service.get_latest_prompt_trace(scope)
    log_debug(
        "http.get_prompt_trace.completed",
        category=LOG_CATEGORY_FRONTEND_BACKEND,
        session_id=session_id,
        agent_id=agent_id,
        run_id=run_id,
        found=trace is not None,
    )
    return SessionPromptTraceResponse(
        session_id=session_id,
        agent_id=agent_id,
        prompt_trace=(
            None
            if trace is None
            else PromptTraceSnapshot(
                id=trace.id,
                session_id=trace.session_id,
                run_id=trace.run_id,
                agent_id=trace.agent_id,
                llm_model=trace.llm_model,
                max_context_tokens=trace.max_context_tokens,
                prompt_budget=trace.prompt_budget,
                estimated_prompt_tokens=trace.estimated_prompt_tokens,
                rendered_message_count=trace.rendered_message_count,
                request_messages=trace.request_messages,
                actions=trace.actions,
                created_at=trace.created_at,
            )
        ),
    )
