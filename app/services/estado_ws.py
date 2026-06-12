"""Publicación de cambios de estado del incidente vía WebSocket."""
from datetime import datetime, timezone

from sqlalchemy import text

from .notifications import notify_estado_change
from ..ws.manager import manager


async def emit_estado_cambio(
    db,
    tenant_id: str,
    incidente_id: str,
    estado_anterior: str,
    estado_nuevo: str,
    *,
    comentario: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> None:
    if estado_anterior == estado_nuevo:
        return

    await manager.publish(
        tenant_id,
        incidente_id,
        {
            "type": "STATUS_CHANGED",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "estado_anterior": estado_anterior,
                "estado_nuevo": estado_nuevo,
                "comentario": comentario,
            },
        },
    )
    if estado_nuevo == "EN_ATENCION" and lat is not None and lng is not None:
        await manager.broadcast_tech_arrived(tenant_id, incidente_id, lat=lat, lng=lng)
    await notify_estado_change(db, tenant_id, incidente_id, estado_anterior, estado_nuevo)


async def marcar_llegada_tecnico(
    db,
    tenant_id: str,
    incidente_id: str,
    lat: float,
    lng: float,
) -> str:
    """Actualiza incidente a EN_ATENCION si aplica. Retorna el estado final."""
    row = db.execute(
        text("SELECT estado FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not row:
        return "UNKNOWN"

    actual = str(row["estado"])
    if actual == "EN_ATENCION":
        return actual
    if actual != "EN_CAMINO":
        return actual

    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'EN_ATENCION' WHERE id = :id"),
        {"id": incidente_id},
    )
    db.commit()
    await emit_estado_cambio(
        db, tenant_id, incidente_id, "EN_CAMINO", "EN_ATENCION", lat=lat, lng=lng
    )
    return "EN_ATENCION"
