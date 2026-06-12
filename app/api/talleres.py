import uuid
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user_verified, get_db, require_permission
from ..core.security import hash_password
from ..services.email import send_temp_password_email
from ..services.pricing import pricing_as_dict
from ..services.tenant_limits import assert_tenant_can_create_workshop

router = APIRouter(prefix="/talleres", tags=["talleres"])


def _resolve_taller_oteecnico(db, user) -> tuple[str, bool, str | None]:
    """Devuelve (taller_id, is_tecnico, tecnico_id) para un usuario TALLER o TECNICO.

    Si el usuario es TALLER: retorna (taller.id, False, None)
    Si el usuario es TECNICO: retorna (tecnico.taller_id, True, tecnico.id)
    Si no se encuentra ninguno: None
    """
    # Buscar como taller
    taller = db.execute(
        text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if taller:
        return str(taller["id"]), False, None

    # Buscar como técnico
    tec = db.execute(
        text("SELECT id, taller_id FROM emergencias.tecnico WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    if tec:
        return str(tec["taller_id"]), True, str(tec["id"])

    return None


def _enrich_item(row, db) -> dict:
    """Aplica pricing y formatea un row de asignación o candidato."""
    item = dict(row)
    pricing = pricing_as_dict(
        item.get("tipo_codigo"),
        item.get("incidente_prioridad"),
        item.get("distancia_km"),
        item.get("taller_calificacion"),
        item.get("carga"),
    )
    if item.get("precio_sugerido") is not None:
        ps = float(item["precio_sugerido"])
        pricing["precio_sugerido"] = ps
        pricing["precio_min"] = round(ps * 0.85, 2)
        pricing["precio_max"] = round(ps * 1.25, 2)
        pricing["comision_plataforma"] = round(ps * 0.10, 2)
        pricing["monto_taller"] = round(ps - pricing["comision_plataforma"], 2)
    if item.get("tiempo_llegada_min") is not None:
        pricing["tiempo_llegada_min"] = int(item["tiempo_llegada_min"])
    if item.get("dificultad") is not None:
        pricing["dificultad"] = item["dificultad"]
    item.update(pricing)
    item.pop("taller_calificacion", None)
    item.pop("tipo_codigo", None)
    item.pop("carga", None)
    return item

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


class TallerUpdate(BaseModel):
    nombre: str | None = None
    direccion: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    telefono: str | None = None
    capacidad_max: int | None = None
    disponible: bool | None = None
    activo: bool | None = None


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


def _enrich_taller_row(row) -> dict:
    return dict(row)


def _fetch_taller(db, taller_id: str) -> dict | None:
    row = db.execute(
        text(
            """SELECT t.*, u.email AS usuario_email, u.nombre AS usuario_nombre
               FROM emergencias.taller t
               LEFT JOIN emergencias.usuario u ON u.id = t.usuario_id
               WHERE t.id = :id"""
        ),
        {"id": taller_id},
    ).mappings().first()
    return _enrich_taller_row(row) if row else None


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
    tupla=Depends(require_permission("taller", "leer")),
):
    user, perm, db = tupla
    sql = """SELECT t.*, u.email AS usuario_email, u.nombre AS usuario_nombre
             FROM emergencias.taller t
             LEFT JOIN emergencias.usuario u ON u.id = t.usuario_id"""
    params: dict = {}
    if user.rol == "TALLER":
        sql += " WHERE t.usuario_id = :uid"
        params["uid"] = user.id
    sql += " ORDER BY t.nombre"
    rows = db.execute(text(sql), params).mappings().all()
    items = perm.filter_list("taller", [_enrich_taller_row(r) for r in rows])
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
def create_taller(
    body: TallerCreate,
    tupla=Depends(require_permission("taller", "crear")),
):
    user, perm, db = tupla
    assert_tenant_can_create_workshop(db, user.tenant)
    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    temp_password = secrets.token_urlsafe(12)
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, telefono, password_hash, email_verificado, must_change_password)
            VALUES (:id, :t, 'TALLER', :n, :e, :tel, :ph, true, true)"""
        ),
        {
            "id": uid,
            "t": user.tenant,
            "n": body.nombre,
            "e": body.email.lower(),
            "tel": body.telefono,
            "ph": hash_password(temp_password),
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
            "la": body.latitud if body.latitud is not None else -17.7833,
            "lo": body.longitud if body.longitud is not None else -63.1821,
            "tel": body.telefono,
            "cap": body.capacidad_max,
        },
    )
    _seed_default_especialidades(db, user.tenant, tid)
    send_temp_password_email(body.email.lower(), body.nombre, temp_password)
    return {"id": tid, "usuario_id": uid, "password_temporal": temp_password}


@router.patch("/{taller_id}/disponibilidad")
def set_disponibilidad(
    taller_id: str,
    body: DisponibilidadIn,
    tupla=Depends(require_permission("taller", "actualizar")),
):
    user, perm, db = tupla
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
    tupla=Depends(require_permission("taller", "actualizar")),
):
    user, perm, db = tupla
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
    tupla=Depends(require_permission("especialidad_taller", "leer")),
    activo: bool | None = None,
):
    user, perm, db = tupla
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
    tupla=Depends(require_permission("especialidad_taller", "crear")),
):
    user, perm, db = tupla
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
    tupla=Depends(require_permission("especialidad_taller", "actualizar")),
):
    user, perm, db = tupla
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
    tupla=Depends(require_permission("especialidad_taller", "eliminar")),
):
    user, perm, db = tupla
    _assert_taller_access(db, user, taller_id)
    raise HTTPException(
        403,
        "Las especialidades del catálogo no se pueden eliminar; desactívelas si no aplican.",
    )


@router.get("/asignaciones")
def list_mis_asignaciones(
    estado: str | None = None,
    tupla=Depends(require_permission("asignacion", "leer")),
):
    user, perm, db = tupla
    result = _resolve_taller_oteecnico(db, user)
    if not result:
        return {"items": [], "total": 0}
    taller_id, is_tecnico, tec_id = result

    # ---------- Query 1: Asignaciones existentes ----------
    base_sql = """SELECT a.id, a.incidente_id, a.taller_id, a.estado,
                  a.tecnico_id, a.motivo_rechazo, a.respondido_at, a.asignado_at,
                  a.asignacion_automatica,
                  t.nombre AS taller_nombre,
                  t.calificacion AS taller_calificacion,
                  i.descripcion AS incidente_descripcion,
                  i.direccion AS incidente_direccion,
                  i.latitud AS incidente_latitud,
                  i.longitud AS incidente_longitud,
                  i.prioridad AS incidente_prioridad,
                  i.resumen_ia AS incidente_resumen_ia,
                  i.estado AS incidente_estado,
                  i.reportado_at AS incidente_reportado_at,
                  ti.codigo AS tipo_codigo,
                  tc.distancia_km,
                  tc.tiempo_llegada_min,
                  tc.precio_sugerido,
                  tc.dificultad,
                  c.id AS cotizacion_id,
                  c.monto AS precio_ofertado,
                  c.tiempo_estimado_min AS tiempo_ofertado_min,
                  c.estado AS cotizacion_estado,
                  (SELECT count(*) FROM emergencias.asignacion ax
                   WHERE ax.taller_id = a.taller_id
                     AND ax.estado IN ('ASIGNADO', 'ACEPTADO')) AS carga,
                  FALSE AS es_candidato
           FROM emergencias.asignacion a
           JOIN emergencias.taller t ON t.id = a.taller_id
           JOIN emergencias.incidente i ON i.id = a.incidente_id
           LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
           LEFT JOIN emergencias.taller_candidato tc
             ON tc.incidente_id = a.incidente_id AND tc.taller_id = a.taller_id
           LEFT JOIN emergencias.cotizacion c ON c.asignacion_id = a.id
           WHERE a.taller_id = :tid"""
    params: dict = {"tid": taller_id}
    # Si es técnico, filtrar solo sus asignaciones
    if is_tecnico and tec_id:
        base_sql += " AND (a.tecnico_id = :tec_id OR a.tecnico_id IS NULL)"
        params["tec_id"] = tec_id
    if estado:
        base_sql += " AND a.estado = :est"
        params["est"] = estado

    rows_asig = db.execute(text(base_sql), params).mappings().all()
    items: list[dict] = []
    seen_incidentes = set()
    for row in rows_asig:
        item = _enrich_item(row, db)
        seen_incidentes.add(item["incidente_id"])
        items.append(item)

    # ---------- Query 2: Candidatos pendientes (incidentes sin asignar) ----------
    if not estado or estado in ("PENDIENTE", "ASIGNADO"):
        candidato_sql = """SELECT tc.id, tc.incidente_id, tc.taller_id,
                          'PENDIENTE' AS estado,
                          NULL AS tecnico_id,
                          NULL AS motivo_rechazo,
                          NULL AS respondido_at,
                          NULL AS asignado_at,
                          NULL AS asignacion_automatica,
                          t.nombre AS taller_nombre,
                          t.calificacion AS taller_calificacion,
                          i.descripcion AS incidente_descripcion,
                          i.direccion AS incidente_direccion,
                          i.latitud AS incidente_latitud,
                          i.longitud AS incidente_longitud,
                          i.prioridad AS incidente_prioridad,
                          i.resumen_ia AS incidente_resumen_ia,
                          i.estado AS incidente_estado,
                          i.reportado_at AS incidente_reportado_at,
                          ti.codigo AS tipo_codigo,
                          tc.distancia_km,
                          tc.tiempo_llegada_min,
                          tc.precio_sugerido,
                          tc.dificultad,
                          NULL AS cotizacion_id,
                          NULL AS precio_ofertado,
                          NULL AS tiempo_ofertado_min,
                          NULL AS cotizacion_estado,
                          (SELECT count(*) FROM emergencias.asignacion ax
                           WHERE ax.taller_id = tc.taller_id
                             AND ax.estado IN ('ASIGNADO', 'ACEPTADO')) AS carga
                   FROM emergencias.taller_candidato tc
                   JOIN emergencias.taller t ON t.id = tc.taller_id
                   JOIN emergencias.incidente i ON i.id = tc.incidente_id
                   LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
                   WHERE tc.taller_id = :tid
                     AND i.estado IN ('PENDIENTE', 'BUSCANDO_TALLER')
                     AND NOT EXISTS (
                       SELECT 1 FROM emergencias.asignacion a2
                       WHERE a2.incidente_id = tc.incidente_id
                         AND a2.taller_id = tc.taller_id
                     )"""
        params_cand: dict = {"tid": taller_id}
        if estado:
            # Si filtra por estado específico, mostrar candidatos solo cuando el incidente
            # está en el estado esperado (PENDIENTE → se ve como "ASIGNADO" para el taller)
            pass  # ya filtrado por i.estado

        rows_cand = db.execute(text(candidato_sql), params_cand).mappings().all()
        for row in rows_cand:
            iid = str(row["incidente_id"])
            if iid not in seen_incidentes:
                item = _enrich_item(row, db)
                item["es_candidato"] = True
                seen_incidentes.add(iid)
                items.append(item)

    items.sort(
        key=lambda x: str(
            x.get("incidente_reportado_at")
            or x.get("asignado_at")
            or x.get("respondido_at")
            or ""
        ),
        reverse=True,
    )
    return {"items": items, "total": len(items)}


@router.get("/notificaciones")
def list_mis_notificaciones(
    tupla=Depends(require_permission("notificacion", "leer")),
):
    user, perm, db = tupla
    # Notificaciones van directo al usuario (no necesitan resolver taller)
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
    tupla=Depends(require_permission("taller", "leer")),
):
    user, perm, db = tupla
    taller_id, is_tecnico, _ = _resolve_taller_oteecnico(db, user) or (None, False, None)
    if not taller_id:
        raise HTTPException(404, "Taller no encontrado")
    row = db.execute(
        text("SELECT * FROM emergencias.taller WHERE id = :id"),
        {"id": taller_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Taller no encontrado")
    result = dict(row)
    result["es_tecnico"] = is_tecnico
    return result


@router.put("/disponibilidad")
def set_disponibilidad_mio(
    body: DisponibilidadIn,
    tupla=Depends(require_permission("taller", "actualizar")),
):
    user, perm, db = tupla
    taller_id, is_tecnico, _ = _resolve_taller_oteecnico(db, user) or (None, False, None)
    if not taller_id:
        raise HTTPException(404, "Taller no encontrado")

    sql = "UPDATE emergencias.taller SET disponible = :d"
    params: dict = {"d": body.disponible, "tid": taller_id}
    if body.capacidad_max is not None:
        sql += ", capacidad_max = :cap"
        params["cap"] = body.capacidad_max
    sql += " WHERE id = :tid"
    db.execute(text(sql), params)
    return {"ok": True}


@router.get("/{taller_id}")
def get_taller(
    taller_id: str,
    tupla=Depends(require_permission("taller", "leer")),
):
    user, perm, db = tupla
    _assert_taller_access(db, user, taller_id)
    row = _fetch_taller(db, taller_id)
    if not row:
        raise HTTPException(404, "Taller no encontrado")
    return perm.filter_dict("taller", row)


@router.patch("/{taller_id}")
def update_taller(
    taller_id: str,
    body: TallerUpdate,
    tupla=Depends(require_permission("taller", "actualizar")),
):
    user, perm, db = tupla
    _assert_taller_access(db, user, taller_id)
    sets: list[str] = []
    params: dict = {"id": taller_id}
    field_map = {
        "nombre": body.nombre,
        "direccion": body.direccion,
        "latitud": body.latitud,
        "longitud": body.longitud,
        "telefono": body.telefono,
        "capacidad_max": body.capacidad_max,
        "disponible": body.disponible,
        "activo": body.activo,
    }
    for col, val in field_map.items():
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if not sets:
        raise HTTPException(400, "Nada que actualizar")
    sql = f"UPDATE emergencias.taller SET {', '.join(sets)} WHERE id = :id"
    if user.rol == "TALLER":
        sql += " AND usuario_id = :uid"
        params["uid"] = user.id
    result = db.execute(text(sql), params)
    if result.rowcount == 0:
        raise HTTPException(404, "Taller no encontrado")
    row = _fetch_taller(db, taller_id)
    return perm.filter_dict("taller", row) if row else {"ok": True}


@router.delete("/{taller_id}")
def delete_taller(
    taller_id: str,
    tupla=Depends(require_permission("taller", "eliminar")),
):
    user, perm, db = tupla
    if user.rol == "TALLER":
        raise HTTPException(403, "Solo el administrador del tenant puede desactivar talleres")
    _assert_taller_access(db, user, taller_id)
    result = db.execute(
        text("UPDATE emergencias.taller SET activo = FALSE WHERE id = :id"),
        {"id": taller_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Taller no encontrado")
    return {"ok": True}


# ------------------------------------------------------------------
# Candidatos — aceptar / rechazar incidentes sin asignar
# ------------------------------------------------------------------

class CandidatoAceptar(BaseModel):
    precio_ofertado: float | None = None
    tiempo_estimado_min: int | None = None
    tecnico_id: str | None = None
    comentario: str | None = None


class CandidatoRechazar(BaseModel):
    motivo: str | None = None


def _resolve_candidato(db, candidato_id: str, user):
    """Retorna (candidato_row, taller_id del usuario, es_tecnico, tecnico_id) o aborta."""
    result = _resolve_taller_oteecnico(db, user)
    if not result:
        raise HTTPException(404, "No se resolvió taller/técnico")
    taller_id, is_tecnico, tec_id = result

    cand = db.execute(
        text("""
            SELECT tc.id, tc.incidente_id, tc.taller_id, tc.distancia_km,
                   tc.tiempo_llegada_min, tc.precio_sugerido, tc.dificultad,
                   i.estado AS incidente_estado
            FROM emergencias.taller_candidato tc
            JOIN emergencias.incidente i ON i.id = tc.incidente_id
            WHERE tc.id = :cid AND tc.taller_id = :tid
        """),
        {"cid": candidato_id, "tid": taller_id},
    ).mappings().first()
    if not cand:
        raise HTTPException(404, "Candidato no encontrado")
    if cand["incidente_estado"] not in ("PENDIENTE", "BUSCANDO_TALLER"):
        raise HTTPException(409, "El incidente ya fue asignado")
    return cand, taller_id, is_tecnico, tec_id


@router.post("/candidatos/{candidato_id}/aceptar", status_code=201)
async def aceptar_candidato(
    candidato_id: str,
    body: CandidatoAceptar,
    tupla=Depends(require_permission("asignacion", "crear")),
):
    user, perm, db = tupla
    cand, taller_id, is_tecnico, tec_id = _resolve_candidato(db, candidato_id, user)

    taller_tenant = db.execute(
        text("SELECT tenant_id FROM emergencias.taller WHERE id = :id"),
        {"id": taller_id},
    ).scalar()

    # Determinar técnico
    tecnico_id = body.tecnico_id
    if not tecnico_id:
        from ..services.access import best_tecnico_for_assignment
        tecnico_id = best_tecnico_for_assignment(
            db, taller_id,
            None  # tipo_incidente no disponible del candidato directamente
        )

    # Crear asignación
    asig_id = str(uuid.uuid4())
    db.execute(
        text("""
            INSERT INTO emergencias.asignacion
            (id, tenant_id, incidente_id, taller_id, tecnico_id, estado,
             asignacion_automatica, respondido_at)
            VALUES (:id, :t, :i, :tl, :tec, 'ASIGNADO', FALSE, now())
        """),
        {"id": asig_id, "t": taller_tenant, "i": str(cand["incidente_id"]),
         "tl": taller_id, "tec": tecnico_id},
    )

    # Crear cotización si se ofrece precio
    if body.precio_ofertado is not None and body.precio_ofertado > 0:
        cot_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO emergencias.cotizacion
                (id, tenant_id, incidente_id, taller_id, asignacion_id, origen,
                 monto, precio_sugerido, tiempo_estimado_min, tiempo_llegada_min,
                 dificultad, detalle, comentario_taller, estado)
                VALUES (:id, :t, :i, :tl, :a, 'TALLER', :m, :ps, :tt, :tlleg,
                        :dif, :detalle, :com, 'PENDIENTE')
            """),
            {
                "id": cot_id, "t": taller_tenant, "i": str(cand["incidente_id"]),
                "tl": taller_id, "a": asig_id,
                "m": body.precio_ofertado,
                "ps": cand["precio_sugerido"],
                "tt": body.tiempo_estimado_min,
                "tlleg": cand["tiempo_llegada_min"],
                "dif": cand["dificultad"],
                "detalle": body.comentario or f"Oferta del taller",
                "com": body.comentario,
            },
        )

    # Cambiar estado del incidente a TALLER_ASIGNADO
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'TALLER_ASIGNADO' WHERE id = :i"),
        {"i": str(cand["incidente_id"])},
    )
    db.commit()
    return {"asignacion_id": asig_id}


@router.post("/candidatos/{candidato_id}/rechazar")
async def rechazar_candidato(
    candidato_id: str,
    body: CandidatoRechazar,
    tupla=Depends(require_permission("asignacion", "crear")),
):
    user, perm, db = tupla
    # Solo validamos que el candidato existe y pertenece a este taller
    result = _resolve_taller_oteecnico(db, user)
    if not result:
        raise HTTPException(404, "No se resolvió taller/técnico")
    taller_id, _, _ = result

    cand = db.execute(
        text("""
            SELECT tc.id, tc.incidente_id
            FROM emergencias.taller_candidato tc
            JOIN emergencias.incidente i ON i.id = tc.incidente_id
            WHERE tc.id = :cid AND tc.taller_id = :tid
        """),
        {"cid": candidato_id, "tid": taller_id},
    ).mappings().first()
    if not cand:
        raise HTTPException(404, "Candidato no encontrado")

    # Registrar rechazo (opcional: crear registro de auditoría)
    db.execute(
        text("""
            INSERT INTO emergencias.auditoria (tenant_id, usuario_id, accion, entidad, entidad_id, detalle)
            VALUES (NULL, :uid, 'RECHAZO_CANDIDATO', 'taller_candidato', :cid,
                    jsonb_build_object('motivo', :m, 'incidente_id', :iid))
        """),
        {"uid": user.id, "cid": candidato_id, "m": body.motivo, "iid": str(cand["incidente_id"])},
    )
    db.commit()
    return {"ok": True}
