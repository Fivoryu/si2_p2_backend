import base64
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user, get_db, require_roles
from ..core.state_machine import CANCELABLE, can_transition
from ..schemas.incidente import (
    CancelarIn,
    EstadoPatch,
    IncidenteCreate,
    IncidenteOut,
    UbicacionIn,
)
from ..services.ai import run_ai_pipeline
from ..ws.manager import manager

router = APIRouter(prefix="/incidentes", tags=["incidentes"])


def _row_to_out(row) -> IncidenteOut:
    return IncidenteOut(
        id=row["id"],
        estado=row["estado"],
        prioridad=row["prioridad"],
        tipo_incidente_id=row.get("tipo_incidente_id"),
        latitud=float(row["latitud"]) if row.get("latitud") is not None else None,
        longitud=float(row["longitud"]) if row.get("longitud") is not None else None,
        resumen_ia=row.get("resumen_ia"),
        reportado_at=row["reportado_at"],
        vehiculo_id=row.get("vehiculo_id"),
        descripcion=row.get("descripcion"),
    )


@router.post("", status_code=201, response_model=IncidenteOut)
def crear_incidente(
    body: IncidenteCreate,
    bg: BackgroundTasks,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    inc_id = str(uuid.uuid4())
    ext = str(body.external_id) if body.external_id else None
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, estado, prioridad,
             descripcion, latitud, longitud, direccion, external_id, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, 'PENDIENTE', 'INCIERTA',
                    :d, :la, :lo, :dir, :ext, 'SINCRONIZADO')"""
        ),
        {
            "id": inc_id,
            "t": user.tenant,
            "c": user.id,
            "v": str(body.vehiculo_id),
            "d": body.descripcion,
            "la": body.latitud,
            "lo": body.longitud,
            "dir": body.direccion,
            "ext": ext,
        },
    )
    row = db.execute(
        text("SELECT * FROM emergencias.incidente WHERE id = :id"),
        {"id": inc_id},
    ).mappings().first()
    bg.add_task(run_ai_pipeline, inc_id, user.tenant)
    return _row_to_out(row)


@router.get("")
def list_incidentes(
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
    estado: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    where = "WHERE 1=1"
    params = {"limit": limit, "offset": offset}
    if user.rol == "CONDUCTOR":
        where += " AND conductor_id = :uid"
        params["uid"] = user.id
    elif user.rol == "TALLER":
        where += """ AND id IN (
            SELECT incidente_id FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE t.usuario_id = :uid)"""
        params["uid"] = user.id
    if estado:
        where += " AND estado = :estado"
        params["estado"] = estado
    total = db.execute(
        text(f"SELECT count(*) FROM emergencias.incidente {where}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id, estado, prioridad, tipo_incidente_id, latitud, longitud,
                resumen_ia, reportado_at, vehiculo_id, descripcion
            FROM emergencias.incidente {where}
            ORDER BY reportado_at DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    items = [_row_to_out(r) for r in rows]
    return {"items": items, "total": total}


@router.get("/{incidente_id}")
def get_incidente(
    incidente_id: str,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    inc = db.execute(
        text("SELECT * FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    evs = db.execute(
        text("SELECT * FROM emergencias.evidencia WHERE incidente_id = :id"),
        {"id": incidente_id},
    ).mappings().all()
    asig = db.execute(
        text(
            """SELECT a.*, t.nombre AS taller_nombre FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE a.incidente_id = :id ORDER BY a.created_at DESC LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().first()
    return {
        "incidente": dict(inc),
        "evidencias": [dict(e) for e in evs],
        "asignacion": dict(asig) if asig else None,
    }


@router.patch("/{incidente_id}/estado")
async def patch_estado(
    incidente_id: str,
    body: EstadoPatch,
    user: CurrentUser = Depends(require_roles("TALLER", "ADMIN_TENANT", "TECNICO")),
    db=Depends(get_db),
):
    inc = db.execute(
        text("SELECT estado, tenant_id FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    old = inc["estado"]
    if not can_transition(old, body.estado):
        raise HTTPException(409, f"Invalid transition {old} -> {body.estado}")
    db.execute(
        text("UPDATE emergencias.incidente SET estado = :e WHERE id = :id"),
        {"e": body.estado, "id": incidente_id},
    )
    tenant = str(inc["tenant_id"])
    await manager.publish(
        tenant,
        incidente_id,
        {
            "type": "STATUS_CHANGED",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "estado_anterior": old,
                "estado_nuevo": body.estado,
                "comentario": body.comentario,
            },
        },
    )
    if body.estado == "EN_ATENCION":
        await manager.publish(
            tenant,
            incidente_id,
            {
                "type": "TECH_ARRIVED",
                "incident_id": incidente_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "data": {},
            },
        )
    return {"estado": body.estado}


@router.post("/{incidente_id}/cancelar")
def cancelar(
    incidente_id: str,
    body: CancelarIn,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    inc = db.execute(
        text(
            "SELECT estado FROM emergencias.incidente WHERE id = :id AND conductor_id = :c"
        ),
        {"id": incidente_id, "c": user.id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    if inc["estado"] not in CANCELABLE:
        raise HTTPException(409, "Cannot cancel in current state")
    db.execute(
        text(
            """UPDATE emergencias.incidente
            SET estado = 'CANCELADO', motivo_cancelacion = :m WHERE id = :id"""
        ),
        {"m": body.motivo, "id": incidente_id},
    )
    return {"estado": "CANCELADO"}


@router.post("/{incidente_id}/evidencias", status_code=201)
async def add_evidencia(
    incidente_id: str,
    tipo: str = Form(...),
    texto: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    url = None
    if file:
        data = await file.read()
        key = f"{user.tenant}/{incidente_id}/{uuid.uuid4()}"
        try:
            from ..core.aws import upload_bytes

            upload_bytes(key, data, file.content_type or "application/octet-stream")
            url = key
        except Exception:
            url = f"local://{key}"
    eid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.evidencia
            (id, tenant_id, incidente_id, tipo, url, contenido_texto)
            VALUES (:id, :t, :i, :tipo, :url, :txt)"""
        ),
        {
            "id": eid,
            "t": user.tenant,
            "i": incidente_id,
            "tipo": tipo,
            "url": url,
            "txt": texto,
        },
    )
    return {"id": eid, "url": url}


@router.get("/{incidente_id}/historial")
def historial(incidente_id: str, db=Depends(get_db)):
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.incidente_estado_historial
            WHERE incidente_id = :id ORDER BY created_at"""
        ),
        {"id": incidente_id},
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/{incidente_id}/ubicacion")
async def post_ubicacion(
    incidente_id: str,
    body: UbicacionIn,
    user: CurrentUser = Depends(require_roles("TALLER", "TECNICO")),
    db=Depends(get_db),
):
    inc = db.execute(
        text("SELECT tenant_id FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    db.execute(
        text(
            """INSERT INTO emergencias.ubicacion_tracking
            (tenant_id, incidente_id, latitud, longitud, tecnico_id)
            VALUES (:t, :i, :la, :lo, :tec)"""
        ),
        {
            "t": str(inc["tenant_id"]),
            "i": incidente_id,
            "la": body.lat,
            "lo": body.lng,
            "tec": body.tecnico_id or user.id,
        },
    )
    tenant = str(inc["tenant_id"])
    await manager.publish(
        tenant,
        incidente_id,
        {
            "type": "TECH_LOCATION",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"lat": body.lat, "lng": body.lng, "tecnico_id": body.tecnico_id},
        },
    )
    return {"ok": True}
