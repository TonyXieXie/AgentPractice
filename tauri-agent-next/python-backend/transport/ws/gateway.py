from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from transport.ws.ws_types import (
    WsAckFrame,
    WsErrorFrame,
    WsHeartbeatFrame,
    WsInboundMessage,
)


router = APIRouter()


@router.websocket("/ws")
async def websocket_gateway(websocket: WebSocket) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    hub = services.ws_hub
    observation_center = services.observation_center
    connection = await hub.register(websocket)
    await websocket.send_json(
        WsAckFrame(
            connection_id=connection.id,
            message="connected",
        ).model_dump()
    )
    try:
        while True:
            raw_message = await websocket.receive_json()
            message = WsInboundMessage.model_validate(raw_message)
            if message.kind == "heartbeat":
                await websocket.send_json(
                    WsHeartbeatFrame(
                        connection_id=connection.id,
                    ).model_dump()
                )
                continue
            if message.kind == "subscribe":
                await hub.subscribe(connection, message.scopes)
            elif message.kind == "unsubscribe":
                await hub.unsubscribe(connection, message.scopes)
            elif message.kind == "set_scope":
                await hub.set_scope(connection, message.scopes)
            elif message.kind == "resume":
                run_id, replayed = await observation_center.replay_connection(
                    connection,
                    after_seq=max(0, int(message.after_seq or 0)),
                )
                if run_id is None:
                    await websocket.send_json(
                        WsErrorFrame(
                            connection_id=connection.id,
                            message="resume requires a scope that resolves to exactly one run_id",
                        ).model_dump()
                    )
                    continue
                await websocket.send_json(
                    WsAckFrame(
                        connection_id=connection.id,
                        message="resume",
                        payload={"run_id": run_id, "replayed": replayed},
                    ).model_dump()
                )
                continue
            await websocket.send_json(
                WsAckFrame(
                    connection_id=connection.id,
                    message=message.kind,
                    payload={"scopes": [scope.model_dump() for scope in message.scopes]},
                ).model_dump()
            )
    except WebSocketDisconnect:
        await hub.unregister(connection)
    except Exception as exc:
        await websocket.send_json(
            WsErrorFrame(
                connection_id=connection.id,
                message=str(exc),
            ).model_dump()
        )
        await hub.unregister(connection)
