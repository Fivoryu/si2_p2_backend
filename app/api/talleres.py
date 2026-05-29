import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user, get_db, require_roles
from ..core.security import hash_password

router = APIRouter(prefix="/talleres", tags=["talleres"])


class TallerCreate(BaseModel):
    nombre: str
    direccion: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    telefono: str | None = None
    email: EmailStr
    capacidad_max: int = 3


class DisponibilidadIn(BaseModel):
    disponible: bool
    capacidad_max: int | None = None


class ServiciosIn(BaseModel):
    tipo_incidente_ids: list[str]


@router.get("")
def list_talleres(
    user: CurrentUser = Depends(get_current_user),  # noqa: ARG001
    db=Depends(get_db),
):
    rows = db.execute(
        text("SELECT * FROM emergencias.taller ORDER BY nombre"),
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("", status_code=201)
def create_taller(
    body: TallerCreate,
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT")),
    db=Depends(get_db),
):
    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, telefono, password_hash, email_verificado)
            VALUES (:id, :t, 'TALLER', :n, :e, :tel, :ph, true)"""
        ),
        {
            "id": uid,
            "t": user.tenant,
            "n": body.nombre,
            "e": body.email.lower(),
            "tel": body.telefono,
            "ph": hash_password("password123"),
        },
    )
    db.execute(
        text(
            """INSERT INTO emergencias.taller
            (id, tenant_id, usuario_id, nombre, direccion, latitud, longitud, telefono, capacidad_max)
            VALUES (:id, :t, :u, :n, :d, :la, :lo, :tel, :cap)"""
        ),
        {
            "id": tid,
            "t": user.tenant,
            "u": uid,
            "n": body.nombre,
            "d": body.direccion,
            "la": body.latitud,
            "lo": body.longitud,
            "tel": body.telefono,
            "cap": body.capacidad_max,
        },
    )
    return {"id": tid, "usuario_id": uid}


@router.patch("/{taller_id}/disponibilidad")
def set_disponibilidad(
    taller_id: str,
    body: DisponibilidadIn,
    user: CurrentUser = Depends(require_roles("TALLER", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    sql = "UPDATE emergencias.taller SET disponible = :d"
    params = {"d": body.disponible, "id": taller_id}
    if body.capacidad_max is not None:
        sql += ", capacidad_max = :cap"
        params["cap"] = body.capacidad_max
    sql += " WHERE id = :id"
    if user.rol == "TALLER":
        sql += " AND usuario_id = :uid"
        params["uid"] = user.id
    db.execute(text(sql), params)
    return {"ok": True}


@router.post("/{taller_id}/servicios")
def set_servicios(
    taller_id: str,
    body: ServiciosIn,
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    for tipo_id in body.tipo_incidente_ids:
        db.execute(
            text(
                """INSERT INTO emergencias.taller_servicio (taller_id, tipo_incidente_id)
                VALUES (:t, :tp) ON CONFLICT DO NOTHING"""
            ),
            {"t": taller_id, "tp": tipo_id},
        )
    return {"ok": True}
