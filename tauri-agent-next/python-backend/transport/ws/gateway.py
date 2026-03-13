from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from transport.ws.ws_types import (
    WsAckFrame,
    WsErrorFrame,
    WsHeartbeatFrame,
    WsHeartbeatMessage,
    WsRequestBootstrapMessage,
    WsResumePrivateMessage,
    WsResumeSharedMessage,
    WsSetScopeMessage,
)


router = APIRouter()


@router.websocket("/ws")
async def websocket_gateway(websocket: WebSocket) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    manager = services.ws_session_manager
    session = await manager.register(websocket)
    await websocket.send_json(
        WsAckFrame(
            ws_session_id=session.ws_session_id,
            message="connected",
        ).model_dump(mode="json")
    )
    try:
        while True:
            raw_message = await websocket.receive_json()
            kind = str(raw_message.get("kind") or "").strip()
            if kind == "heartbeat":
                WsHeartbeatMessage.model_validate(raw_message)
                await websocket.send_json(
                    WsHeartbeatFrame(
                        ws_session_id=session.ws_session_id,
                    ).model_dump(mode="json")
                )
                continue
            if kind == "set_scope":
                message = WsSetScopeMessage.model_validate(raw_message)
                await manager.set_scope(
                    session,
                    viewer_id=message.viewer_id,
                    target_session_id=message.target_session_id,
                    selected_run_id=message.selected_run_id,
                    selected_agent_id=message.selected_agent_id,
                    include_private=message.include_private,
                )
                await websocket.send_json(
                    WsAckFrame(
                        ws_session_id=session.ws_session_id,
                        message="set_scope",
                        payload={
                            "target_session_id": message.target_session_id,
                            "selected_run_id": message.selected_run_id,
                            "selected_agent_id": message.selected_agent_id,
                            "include_private": message.include_private,
                        },
                    ).model_dump(mode="json")
                )
                continue
            if kind == "request_bootstrap":
                message = WsRequestBootstrapMessage.model_validate(raw_message)
                shared_after_seq, private_after_id = await manager.request_bootstrap(
                    session,
                    shared_limit=message.shared_limit,
                    private_limit=message.private_limit,
                )
                await websocket.send_json(
                    WsAckFrame(
                        ws_session_id=session.ws_session_id,
                        message="request_bootstrap",
                        payload={
                            "shared_after_seq": shared_after_seq,
                            "private_after_id": private_after_id,
                        },
                    ).model_dump(mode="json")
                )
                continue
            if kind == "resume_shared":
                message = WsResumeSharedMessage.model_validate(raw_message)
                replayed = await manager.resume_shared(
                    session,
                    after_seq=message.after_seq,
                    limit=message.limit,
                )
                await websocket.send_json(
                    WsAckFrame(
                        ws_session_id=session.ws_session_id,
                        message="resume_shared",
                        payload={"replayed": replayed},
                    ).model_dump(mode="json")
                )
                continue
            if kind == "resume_private":
                message = WsResumePrivateMessage.model_validate(raw_message)
                replayed = await manager.resume_private(
                    session,
                    after_id=message.after_id,
                    limit=message.limit,
                )
                await websocket.send_json(
                    WsAckFrame(
                        ws_session_id=session.ws_session_id,
                        message="resume_private",
                        payload={"replayed": replayed},
                    ).model_dump(mode="json")
                )
                continue
            await websocket.send_json(
                WsErrorFrame(
                    ws_session_id=session.ws_session_id,
                    message=f"unsupported ws message kind: {kind or '(empty)'}",
                ).model_dump(mode="json")
            )
    except WebSocketDisconnect:
        await manager.unregister(session)
    except Exception as exc:
        await websocket.send_json(
            WsErrorFrame(
                ws_session_id=session.ws_session_id,
                message=str(exc),
            ).model_dump(mode="json")
        )
        await manager.unregister(session)
