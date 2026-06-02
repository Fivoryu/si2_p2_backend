import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles
from ..services.notifications import notify_incident_users
from ..ws.manager import manager

router = APIRouter(tags=["cotizaciones"])


class CotizacionIn(BaseModel):
    monto: float
    detalle: str | None = None
    origen: str = "TALLER"
    taller_id: str | None = None
    tiempo_estimado_min: int | None = None


@router.post("/incidentes/{incidente_id}/cotizaciones", status_code=201)
def crear_cotizacion(
    incidente_id: str,
    body: CotizacionIn,
    user=Depends(require_roles("TALLER", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    taller_id = body.taller_id
    if not taller_id and user.rol == "TALLER":
        row = db.execute(
            text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
            {"uid": user.id},
        ).first()
        if row:
            taller_id = str(row[0])
    if not taller_id:
        raise HTTPException(422, "taller_id es obligatorio")
    cid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.cotizacion
            (id, tenant_id, incidente_id, taller_id, monto, detalle, origen,
             tiempo_estimado_min, estado)
            VALUES (:id, :t, :i, :tl, :m, :d, :o, :tiempo, 'PENDIENTE')"""
        ),
        {
            "id": cid,
            "t": user.tenant,
            "i": incidente_id,
            "tl": taller_id,
            "m": body.monto,
            "d": body.detalle,
            "o": body.origen,
            "tiempo": body.tiempo_estimado_min,
        },
    )
    return {"id": cid}


@router.get("/incidentes/{incidente_id}/ofertas")
def listar_ofertas(
    incidente_id: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    where_owner = ""
    params: dict = {"i": incidente_id}
    if user.rol == "CONDUCTOR":
        where_owner = "AND i.conductor_id = :uid"
        params["uid"] = user.id
    inc = db.execute(
        text(f"SELECT id FROM emergencias.incidente i WHERE i.id = :i {where_owner}"),
        params,
    ).first()
    if not inc:
        raise HTTPException(404, "Incidente no encontrado")

    rows = db.execute(
        text(
            """SELECT c.id, c.incidente_id, c.asignacion_id, c.taller_id, c.monto,
                      c.precio_sugerido, c.tiempo_estimado_min, c.tiempo_llegada_min,
                      c.dificultad, c.detalle, c.comentario_taller, c.estado,
                      c.created_at, t.nombre AS taller_nombre, t.calificacion,
                      tc.distancia_km, tc.puntaje
               FROM emergencias.cotizacion c
               JOIN emergencias.taller t ON t.id = c.taller_id
               LEFT JOIN emergencias.taller_candidato tc
                 ON tc.incidente_id = c.incidente_id AND tc.taller_id = c.taller_id
               WHERE c.incidente_id = :i
               ORDER BY c.estado = 'ACEPTADA' DESC, c.monto ASC, c.created_at ASC"""
        ),
        {"i": incidente_id},
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/cotizaciones/{cotizacion_id}/aceptar")
async def aceptar_cotizacion(
    cotizacion_id: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    return await seleccionar_cotizacion(cotizacion_id, user, db)


@router.post("/cotizaciones/{cotizacion_id}/seleccionar")
async def seleccionar_cotizacion(
    cotizacion_id: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    cot = db.execute(
        text(
            """SELECT c.id, c.incidente_id, c.asignacion_id, c.taller_id, c.tenant_id,
                      i.conductor_id
               FROM emergencias.cotizacion c
               JOIN emergencias.incidente i ON i.id = c.incidente_id
               WHERE c.id = :id AND c.estado = 'PENDIENTE'"""
        ),
        {"id": cotizacion_id},
    ).mappings().first()
    if not cot:
        raise HTTPException(404, "Not found")
    if str(cot["conductor_id"]) != user.id:
        raise HTTPException(403, "No puedes seleccionar esta cotizacion")

    db.execute(
        text(
            """UPDATE emergencias.cotizacion
               SET estado = CASE WHEN id = :id
                                  THEN 'ACEPTADA'::emergencias.estado_cotizacion
                                  ELSE 'RECHAZADA'::emergencias.estado_cotizacion END,
                   updated_at = now()
               WHERE incidente_id = :i AND estado = 'PENDIENTE'"""
        ),
        {"id": cotizacion_id, "i": str(cot["incidente_id"])},
    )
    if cot["asignacion_id"]:
        db.execute(
            text(
                """UPDATE emergencias.asignacion
                   SET estado = CASE WHEN id = :aid
                                      THEN 'ACEPTADO'::emergencias.estado_asignacion
                                      ELSE 'RECHAZADO'::emergencias.estado_asignacion END,
                       respondido_at = COALESCE(respondido_at, now()),
                       motivo_rechazo = CASE WHEN id = :aid THEN motivo_rechazo ELSE 'Oferta no seleccionada por el cliente' END
                   WHERE incidente_id = :i AND estado IN ('ASIGNADO', 'ACEPTADO')"""
            ),
            {"aid": str(cot["asignacion_id"]), "i": str(cot["incidente_id"])},
        )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'EN_CAMINO' WHERE id = :i"),
        {"i": str(cot["incidente_id"])},
    )
    tenant = str(cot["tenant_id"])
    iid = str(cot["incidente_id"])
    await manager.publish(
        tenant,
        iid,
        {
            "type": "STATUS_CHANGED",
            "incident_id": iid,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"estado_nuevo": "EN_CAMINO", "cotizacion_id": cotizacion_id},
        },
    )
    await notify_incident_users(
        db,
        tenant,
        iid,
        "Oferta seleccionada",
        "El cliente selecciono una oferta. El tecnico va en camino.",
    )
    return {"estado": "ACEPTADA"}
