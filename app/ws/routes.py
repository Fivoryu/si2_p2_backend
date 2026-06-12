import json
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from ..core.db import SessionLocal
from ..core.deps import CurrentUser
from ..core.security import decode_token
from ..services.access import can_access_incident, can_manage_incident_service
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
    user = CurrentUser(
        claims["sub"],
        claims["rol"],
        claims.get("tenant"),
        claims.get("jti", ""),
    )

    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": tenant_id},
        )
        revoked = db.execute(
            text("SELECT 1 FROM emergencias.token_revocado WHERE jti = :j"),
            {"j": user.jti},
        ).first()
        if revoked:
            await ws.close(code=4401)
            return
        inc = db.execute(
            text(
                """SELECT id, estado, prioridad, tipo_incidente_id, latitud, longitud, resumen_ia,
                          tiempo_estimado_min, reportado_at, asignado_at, aceptado_at,
                          en_camino_at, atendido_at, finalizado_at
                   FROM emergencias.incidente WHERE id = :id"""
            ),
            {"id": incident_id},
        ).mappings().first()
        if inc and not can_access_incident(db, incident_id, user):
            await ws.close(code=4403)
            return
        asig = db.execute(
            text(
                """SELECT a.id, a.taller_id, a.tecnico_id, a.estado, t.nombre AS taller_nombre,
                          tec.nombre AS tecnico_nombre
                   FROM emergencias.asignacion a
                   JOIN emergencias.taller t ON t.id = a.taller_id
                   LEFT JOIN emergencias.tecnico tec ON tec.id = a.tecnico_id
                   WHERE a.incidente_id = :id
                   ORDER BY a.estado = 'ACEPTADO' DESC, a.created_at DESC
                   LIMIT 1"""
            ),
            {"id": incident_id},
        ).mappings().first()
        last_location = db.execute(
            text(
                """SELECT latitud, longitud, tecnico_id, es_fake, created_at
                   FROM emergencias.ubicacion_tracking
                   WHERE incidente_id = :id
                   ORDER BY created_at DESC
                   LIMIT 1"""
            ),
            {"id": incident_id},
        ).mappings().first()
        history = db.execute(
            text(
                """SELECT estado_anterior, estado_nuevo, created_at
                   FROM emergencias.incidente_estado_historial
                   WHERE incidente_id = :id
                   ORDER BY created_at DESC
                   LIMIT 5"""
            ),
            {"id": incident_id},
        ).mappings().all()
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
                "data": {
                    "incidente": dict(inc),
                    "asignacion": dict(asig) if asig else None,
                    "ultima_ubicacion": dict(last_location) if last_location else None,
                    "eta_min": inc.get("tiempo_estimado_min"),
                    "historial": [dict(h) for h in history],
                },
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
                if user.rol not in {"TALLER", "TECNICO", "ADMIN_TENANT", "ADMIN_PLATAFORMA"}:
                    await ws.send_text(json.dumps({"type": "ERROR", "detail": "TECH_LOCATION forbidden"}))
                    continue
                db = SessionLocal()
                try:
                    db.execute(
                        text("SELECT set_config('app.current_tenant', :t, true)"),
                        {"t": tenant_id},
                    )
                    if not can_manage_incident_service(db, incident_id, user):
                        await ws.send_text(json.dumps({"type": "ERROR", "detail": "Incident forbidden"}))
                        continue
                    data = msg.get("data", {}) or {}
                    params = {
                        "t": tenant_id,
                        "i": incident_id,
                        "la": data.get("lat"),
                        "lo": data.get("lng"),
                        "tec": data.get("tecnico_id"),
                        "fake": bool(data.get("es_fake", False)),
                        "vel": data.get("velocidad_kmh"),
                        "prec": data.get("precision_m"),
                    }
                    if params["la"] is None or params["lo"] is None:
                        await ws.send_text(json.dumps({"type": "ERROR", "detail": "lat/lng required"}))
                        continue
                    try:
                        db.execute(
                            text(
                                """INSERT INTO emergencias.ubicacion_tracking
                                (tenant_id, incidente_id, latitud, longitud, tecnico_id, es_fake,
                                 velocidad_kmh, precision_m)
                                VALUES (:t, :i, :la, :lo, :tec, :fake, :vel, :prec)"""
                            ),
                            params,
                        )
                    except Exception:
                        db.rollback()
                        db.execute(
                            text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": tenant_id},
                        )
                        db.execute(
                            text(
                                """INSERT INTO emergencias.ubicacion_tracking
                                (tenant_id, incidente_id, latitud, longitud, tecnico_id, es_fake)
                                VALUES (:t, :i, :la, :lo, :tec, :fake)"""
                            ),
                            params,
                        )
                    db.commit()
                finally:
                    db.close()
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
