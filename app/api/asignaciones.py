from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles
from ..services.assignment import assign_best_workshop
from ..ws.manager import manager

router = APIRouter(tags=["asignaciones"])


class RechazarIn(BaseModel):
    motivo: str | None = None


class AceptarIn(BaseModel):
    tecnico_id: str | None = None


class ManualAssignIn(BaseModel):
    taller_id: str


@router.post("/incidentes/{incidente_id}/buscar-talleres")
def buscar_talleres(incidente_id: str, db=Depends(get_db)):
    from ..services.assignment import CANDIDATE_SQL

    rows = db.execute(CANDIDATE_SQL, {"inc": incidente_id}).mappings().all()
    return {"candidatos": [dict(r) for r in rows]}


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
    return {"estado": "EN_CAMINO"}


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
    await assign_best_workshop(str(asig["incidente_id"]), str(asig["tenant_id"]), exclude)
    return {"estado": "BUSCANDO_TALLER"}
