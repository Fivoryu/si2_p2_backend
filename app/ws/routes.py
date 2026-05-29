import json
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from ..core.db import SessionLocal
from ..core.security import decode_token
from .manager import manager

router = APIRouter()


@router.websocket("/ws/{tenant_id}/{incident_id}")
async def ws_incident(
    ws: WebSocket,
    tenant_id: str,
    incident_id: str,
    token: str = Query(...),
):
    try:
        claims = decode_token(token)
    except ValueError:
        await ws.close(code=4401)
        return
    if claims.get("tenant") != tenant_id:
        await ws.close(code=4403)
        return

    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": tenant_id},
        )
        inc = db.execute(
            text(
                """SELECT id, estado, prioridad, tipo_incidente_id, latitud, longitud, resumen_ia
                   FROM emergencias.incidente WHERE id = :id"""
            ),
            {"id": incident_id},
        ).mappings().first()
    finally:
        db.close()

    if not inc:
        await ws.close(code=4404)
        return

    await manager.connect(ws, tenant_id, incident_id)
    await ws.send_text(
        json.dumps(
            {
                "type": "STATE_SNAPSHOT",
                "incident_id": incident_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": dict(inc),
            },
            default=str,
        )
    )
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "PING":
                await ws.send_text(json.dumps({"type": "PONG"}))
            elif msg.get("type") == "TECH_LOCATION":
                await manager.publish(
                    tenant_id,
                    incident_id,
                    {
                        "type": "TECH_LOCATION",
                        "incident_id": incident_id,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "data": msg.get("data", {}),
                    },
                )
    except WebSocketDisconnect:
        await manager.disconnect(ws, tenant_id, incident_id)
