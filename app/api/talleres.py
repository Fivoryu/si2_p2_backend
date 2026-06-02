import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user, get_db, require_roles
from ..core.security import hash_password

router = APIRouter(prefix="/talleres", tags=["talleres"])

# Catálogo fijo por taller (mismo en todos los tenants; alineado con 03_seed.sql y AI).
DEFAULT_ESPECIALIDADES: tuple[str, ...] = (
    "Sistema de carga / Alternador",
    "Batería descargada",
    "Baja presión de llanta",
    "Llanta pinchada",
    "Sistema de frenos",
    "Motor / Incidencias",
    "Suspensión / ESP",
    "Airbag / Cinturón",
    "Abolladura por colisión",
    "Rayadura / Arañazo",
    "Grieta o quebrado",
    "Vidrio o lámpara rota",
    "Otros",
)


def _seed_default_especialidades(db, tenant_id: str, taller_id: str) -> None:
    for nombre in DEFAULT_ESPECIALIDADES:
        db.execute(
            text(
                """INSERT INTO emergencias.especialidad_taller
                (id, tenant_id, taller_id, nombre)
                VALUES (:id, :t, :tl, :n)
                ON CONFLICT (taller_id, nombre) DO NOTHING"""
            ),
            {"id": str(uuid.uuid4()), "t": tenant_id, "tl": taller_id, "n": nombre},
        )


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


class EspecialidadCreate(BaseModel):
    nombre: str


class EspecialidadUpdate(BaseModel):
    nombre: str | None = None
    activo: bool | None = None


def _assert_taller_access(db, user: CurrentUser, taller_id: str) -> None:
    sql = "SELECT id FROM emergencias.taller WHERE id = :id"
    params: dict = {"id": taller_id}
    if user.rol == "TALLER":
        sql += " AND usuario_id = :uid"
        params["uid"] = user.id
    row = db.execute(text(sql), params).first()
    if not row:
        raise HTTPException(404, "Taller no encontrado o sin permiso")


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
    _seed_default_especialidades(db, user.tenant, tid)
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


@router.get("/{taller_id}/especialidades")
def list_especialidades(
    taller_id: str,
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
    activo: bool | None = None,
):
    _assert_taller_access(db, user, taller_id)
    count_row = db.execute(
        text(
            "SELECT count(*) AS n FROM emergencias.especialidad_taller WHERE taller_id = :tid"
        ),
        {"tid": taller_id},
    ).mappings().first()
    if not count_row or int(count_row["n"]) == 0:
        _seed_default_especialidades(db, user.tenant, taller_id)
    sql = """SELECT id, taller_id, nombre, activo, created_at, updated_at
             FROM emergencias.especialidad_taller
             WHERE taller_id = :tid"""
    params: dict = {"tid": taller_id}
    if activo is not None:
        sql += " AND activo = :act"
        params["act"] = activo
    sql += " ORDER BY nombre"
    rows = db.execute(text(sql), params).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/{taller_id}/especialidades", status_code=201)
def create_especialidad(
    taller_id: str,
    body: EspecialidadCreate,  # noqa: ARG001
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    _assert_taller_access(db, user, taller_id)
    raise HTTPException(
        403,
        "Las especialidades se crean automáticamente al registrar el taller; "
        "no se pueden agregar manualmente.",
    )


@router.patch("/{taller_id}/especialidades/{especialidad_id}")
def update_especialidad(
    taller_id: str,
    especialidad_id: str,
    body: EspecialidadUpdate,
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    _assert_taller_access(db, user, taller_id)
    if body.nombre is not None:
        raise HTTPException(
            403,
            "El catálogo de especialidades es fijo; solo puede activar o desactivar cada una.",
        )
    sets: list[str] = []
    params: dict = {"id": especialidad_id, "tid": taller_id}
    if body.activo is not None:
        sets.append("activo = :a")
        params["a"] = body.activo
    if not sets:
        raise HTTPException(400, "Nada que actualizar")
    sql = f"UPDATE emergencias.especialidad_taller SET {', '.join(sets)} WHERE id = :id AND taller_id = :tid"
    result = db.execute(text(sql), params)
    if result.rowcount == 0:
        raise HTTPException(404, "Especialidad no encontrada")
    return {"ok": True}


@router.delete("/{taller_id}/especialidades/{especialidad_id}")
def delete_especialidad(
    taller_id: str,
    especialidad_id: str,  # noqa: ARG001
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    _assert_taller_access(db, user, taller_id)
    raise HTTPException(
        403,
        "Las especialidades del catálogo no se pueden eliminar; desactívelas si no aplican.",
    )


@router.get("/asignaciones")
def list_mis_asignaciones(
    estado: str | None = None,
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    taller = db.execute(
        text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if not taller:
        return {"items": [], "total": 0}

    base_sql = """SELECT a.id, a.incidente_id, a.taller_id, a.estado,
                  a.tecnico_id, a.motivo_rechazo, a.respondido_at,
                  a.asignacion_automatica,
                  t.nombre AS taller_nombre,
                  i.estado AS incidente_estado,
                  i.descripcion AS incidente_descripcion,
                  i.direccion AS incidente_direccion,
                  i.latitud AS incidente_latitud,
                  i.longitud AS incidente_longitud,
                  i.prioridad AS incidente_prioridad,
                  i.resumen_ia AS incidente_resumen_ia,
                  tc.distancia_km,
                  tc.tiempo_llegada_min,
                  tc.precio_sugerido,
                  tc.dificultad,
                  c.id AS cotizacion_id,
                  c.monto AS precio_ofertado,
                  c.tiempo_estimado_min AS tiempo_ofertado_min,
                  c.estado AS cotizacion_estado
           FROM emergencias.asignacion a
           JOIN emergencias.taller t ON t.id = a.taller_id
           JOIN emergencias.incidente i ON i.id = a.incidente_id
           LEFT JOIN emergencias.taller_candidato tc
             ON tc.incidente_id = a.incidente_id AND tc.taller_id = a.taller_id
           LEFT JOIN emergencias.cotizacion c ON c.asignacion_id = a.id
           WHERE a.taller_id = :tid"""
    params: dict = {"tid": str(taller["id"])}
    if estado:
        base_sql += " AND a.estado = :est"
        params["est"] = estado
    base_sql += " ORDER BY a.respondido_at DESC NULLS LAST"
    rows = db.execute(text(base_sql), params).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/notificaciones")
def list_mis_notificaciones(
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    taller = db.execute(
        text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if not taller:
        return {"items": [], "total": 0}

    rows = db.execute(
        text(
            """SELECT n.id, n.titulo, n.mensaje, n.canal, n.incidente_id,
                      n.enviada, n.creada_at
               FROM emergencias.notificacion n
               WHERE n.usuario_id = :uid
               ORDER BY n.creada_at DESC
               LIMIT 50"""
        ),
        {"uid": user.id},
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/yo")
def get_mi_taller(
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    row = db.execute(
        text("SELECT * FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Taller no encontrado")
    return dict(row)


@router.put("/disponibilidad")
def set_disponibilidad_mio(
    body: DisponibilidadIn,
    user: CurrentUser = Depends(require_roles("TALLER")),
    db=Depends(get_db),
):
    taller = db.execute(
        text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if not taller:
        raise HTTPException(404, "Taller no encontrado")

    sql = "UPDATE emergencias.taller SET disponible = :d"
    params: dict = {"d": body.disponible, "tid": str(taller["id"])}
    if body.capacidad_max is not None:
        sql += ", capacidad_max = :cap"
        params["cap"] = body.capacidad_max
    sql += " WHERE id = :tid"
    db.execute(text(sql), params)
    return {"ok": True}
