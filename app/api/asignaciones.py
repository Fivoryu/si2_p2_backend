from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles
from ..services.assignment import assign_best_workshop
from ..services.notifications import notify_incident_users
from ..services.pricing import calculate_service_offer
from ..ws.manager import manager

router = APIRouter(tags=["asignaciones"])


class RechazarIn(BaseModel):
    motivo: str | None = None


class AceptarIn(BaseModel):
    tecnico_id: str | None = None


class AceptarConOfertaIn(BaseModel):
    precio_ofertado: float | None = None
    tiempo_estimado_min: int | None = None
    tecnico_id: str | None = None
    comentario: str | None = None


class ManualAssignIn(BaseModel):
    taller_id: str


@router.post("/incidentes/{incidente_id}/buscar-talleres")
def buscar_talleres(incidente_id: str, db=Depends(get_db)):
    from ..services.assignment import CANDIDATE_SQL

    rows = db.execute(CANDIDATE_SQL, {"inc": incidente_id}).mappings().all()
    candidatos = []
    for row in rows:
        item = dict(row)
        pricing = calculate_service_offer(
            item.get("tipo_codigo"),
            item.get("prioridad"),
            item.get("distancia_km"),
            item.get("calificacion"),
            item.get("carga"),
        )
        item["precio_sugerido"] = pricing.precio_sugerido
        item["tiempo_llegada_min"] = pricing.tiempo_llegada_min
        item["tiempo_reparacion_min"] = pricing.tiempo_reparacion_min
        item["tiempo_total_min"] = pricing.tiempo_total_min
        item["dificultad"] = pricing.dificultad
        candidatos.append(item)
    return {"candidatos": candidatos}


@router.post("/incidentes/{incidente_id}/asignar")
async def asignar_auto(
    incidente_id: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    await assign_best_workshop(incidente_id, user.tenant)
    return {"ok": True}


@router.post("/incidentes/{incidente_id}/asignar-manual")
async def asignar_manual(
    incidente_id: str,
    body: ManualAssignIn,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    db.execute(
        text(
            """INSERT INTO emergencias.asignacion
            (tenant_id, incidente_id, taller_id, estado, asignacion_automatica)
            VALUES (:t, :i, :tl, 'ASIGNADO', false)"""
        ),
        {"t": user.tenant, "i": incidente_id, "tl": body.taller_id},
    )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'TALLER_ASIGNADO' WHERE id = :i"),
        {"i": incidente_id},
    )
    await manager.publish(
        user.tenant,
        incidente_id,
        {
            "type": "ASSIGNMENT",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"taller_id": body.taller_id, "estado": "ASIGNADO"},
        },
    )
    await notify_incident_users(
        db, user.tenant, incidente_id,
        "Taller Assigned",
        "A workshop has been assigned to your assistance request.",
    )
    return {"ok": True}


@router.post("/asignaciones/{asignacion_id}/aceptar")
async def aceptar(
    asignacion_id: str,
    body: AceptarIn,
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    asig = db.execute(
        text(
            """SELECT a.id, a.incidente_id, a.tenant_id FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE a.id = :id AND t.usuario_id = :u"""
        ),
        {"id": asignacion_id, "u": user.id},
    ).mappings().first()
    if not asig:
        raise HTTPException(404, "Not found")
    db.execute(
        text(
            """UPDATE emergencias.asignacion
            SET estado = 'ACEPTADO', tecnico_id = COALESCE(:tec, tecnico_id), respondido_at = now()
            WHERE id = :id"""
        ),
        {"id": asignacion_id, "tec": body.tecnico_id},
    )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'EN_CAMINO' WHERE id = :i"),
        {"i": str(asig["incidente_id"])},
    )
    tid = str(asig["tenant_id"])
    iid = str(asig["incidente_id"])
    await manager.publish(
        tid,
        iid,
        {
            "type": "STATUS_CHANGED",
            "incident_id": iid,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"estado_nuevo": "EN_CAMINO"},
        },
    )
    await notify_incident_users(
        db, tid, iid,
        "Taller Accepted",
        "Your assistance request has been accepted! A technician is on the way.",
    )
    return {"estado": "EN_CAMINO"}


@router.post("/asignaciones/{asignacion_id}/aceptar-con-oferta", status_code=201)
async def aceptar_con_oferta(
    asignacion_id: str,
    body: AceptarConOfertaIn,
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    asig = db.execute(
        text(
            """SELECT a.id, a.incidente_id, a.taller_id, a.tenant_id,
                      i.prioridad, ti.codigo AS tipo_codigo,
                      tc.distancia_km, tc.tiempo_llegada_min,
                      t.calificacion,
                      (SELECT count(*) FROM emergencias.asignacion ax
                       WHERE ax.taller_id = a.taller_id AND ax.estado IN ('ASIGNADO', 'ACEPTADO')) AS carga
               FROM emergencias.asignacion a
               JOIN emergencias.taller t ON t.id = a.taller_id
               JOIN emergencias.incidente i ON i.id = a.incidente_id
               LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
               LEFT JOIN emergencias.taller_candidato tc
                 ON tc.incidente_id = a.incidente_id AND tc.taller_id = a.taller_id
               WHERE a.id = :id AND t.usuario_id = :u AND a.estado = 'ASIGNADO'"""
        ),
        {"id": asignacion_id, "u": user.id},
    ).mappings().first()
    if not asig:
        raise HTTPException(404, "Asignacion no encontrada o no disponible")

    pricing = calculate_service_offer(
        asig.get("tipo_codigo"),
        asig.get("prioridad"),
        asig.get("distancia_km"),
        asig.get("calificacion"),
        asig.get("carga"),
    )
    precio = round(float(body.precio_ofertado if body.precio_ofertado is not None else pricing.precio_sugerido), 2)
    if precio <= 0:
        raise HTTPException(422, "El precio ofertado debe ser mayor a cero")
    tiempo = body.tiempo_estimado_min or pricing.tiempo_total_min
    if tiempo <= 0:
        raise HTTPException(422, "El tiempo estimado debe ser mayor a cero")

    cot_id = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.cotizacion
            (id, tenant_id, incidente_id, taller_id, asignacion_id, origen, monto,
             precio_sugerido, tiempo_estimado_min, tiempo_llegada_min, dificultad,
             detalle, comentario_taller, estado)
            VALUES (:id, :t, :i, :tl, :a, 'TALLER', :m, :ps, :tt, :tlleg, :dif,
                    :detalle, :comentario, 'PENDIENTE')
            ON CONFLICT (asignacion_id) WHERE asignacion_id IS NOT NULL
            DO UPDATE SET monto = EXCLUDED.monto,
                          precio_sugerido = EXCLUDED.precio_sugerido,
                          tiempo_estimado_min = EXCLUDED.tiempo_estimado_min,
                          tiempo_llegada_min = EXCLUDED.tiempo_llegada_min,
                          dificultad = EXCLUDED.dificultad,
                          detalle = EXCLUDED.detalle,
                          comentario_taller = EXCLUDED.comentario_taller,
                          estado = 'PENDIENTE',
                          updated_at = now()
            RETURNING id"""
        ),
        {
            "id": cot_id,
            "t": user.tenant,
            "i": str(asig["incidente_id"]),
            "tl": str(asig["taller_id"]),
            "a": asignacion_id,
            "m": precio,
            "ps": pricing.precio_sugerido,
            "tt": tiempo,
            "tlleg": pricing.tiempo_llegada_min,
            "dif": pricing.dificultad,
            "detalle": f"Oferta del taller. Precio sugerido: {pricing.precio_sugerido} BOB.",
            "comentario": body.comentario,
        },
    )
    db.execute(
        text(
            """UPDATE emergencias.asignacion
               SET tecnico_id = COALESCE(:tec, tecnico_id), respondido_at = now()
               WHERE id = :id"""
        ),
        {"id": asignacion_id, "tec": body.tecnico_id},
    )
    await notify_incident_users(
        db,
        user.tenant,
        str(asig["incidente_id"]),
        "Nueva oferta recibida",
        "Un taller envio una oferta para tu emergencia.",
    )
    return {
        "cotizacion_id": cot_id,
        "precio_sugerido": pricing.precio_sugerido,
        "precio_ofertado": precio,
        "tiempo_estimado_min": tiempo,
        "estado": "OFERTA_ENVIADA",
    }


@router.post("/asignaciones/{asignacion_id}/rechazar")
async def rechazar(
    asignacion_id: str,
    body: RechazarIn,
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    asig = db.execute(
        text(
            """SELECT a.id, a.incidente_id, a.taller_id, a.tenant_id
            FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE a.id = :id AND t.usuario_id = :u"""
        ),
        {"id": asignacion_id, "u": user.id},
    ).mappings().first()
    if not asig:
        raise HTTPException(404, "Not found")
    db.execute(
        text(
            """UPDATE emergencias.asignacion
            SET estado = 'RECHAZADO', motivo_rechazo = :m, respondido_at = now()
            WHERE id = :id"""
        ),
        {"id": asignacion_id, "m": body.motivo},
    )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'BUSCANDO_TALLER' WHERE id = :i"),
        {"i": str(asig["incidente_id"])},
    )
    rejected = db.execute(
        text(
            """SELECT taller_id FROM emergencias.asignacion
            WHERE incidente_id = :i AND estado = 'RECHAZADO'"""
        ),
        {"i": str(asig["incidente_id"])},
    ).scalars().all()
    exclude = [str(x) for x in rejected]
    db.commit()
    await assign_best_workshop(str(asig["incidente_id"]), str(asig["tenant_id"]), exclude)
    return {"estado": "BUSCANDO_TALLER"}
