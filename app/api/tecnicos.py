import uuid
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from ..core.deps import CurrentUser, require_permission
from ..core.security import hash_password
from ..services.email import send_temp_password_email
from ..services.tenant_limits import assert_tenant_can_create_technician

router = APIRouter(prefix="/tecnicos", tags=["tecnicos"])

_ESPECIALIDAD_MAX_LEN = 80


def _especialidad_summary(labels: list[str]) -> str | None:
    """Resumen legado para tecnico.especialidad (VARCHAR 80)."""
    if not labels:
        return None
    full = ", ".join(labels)
    if len(full) <= _ESPECIALIDAD_MAX_LEN:
        return full
    if len(labels) == 1:
        return full[: _ESPECIALIDAD_MAX_LEN - 3] + "..."
    suffix = f" (+{len(labels) - 1} más)"
    max_prefix = _ESPECIALIDAD_MAX_LEN - len(suffix)
    prefix = labels[0]
    if len(prefix) > max_prefix:
        prefix = prefix[: max_prefix - 3] + "..."
    return prefix + suffix


class TecnicoIn(BaseModel):
    taller_id: str
    nombre: str
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    telefono: str | None = None
    especialidad: str | None = None
    especialidad_ids: list[str] | None = None


class TecnicoUpdate(BaseModel):
    nombre: str | None = None
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)
    telefono: str | None = None
    especialidad_ids: list[str] | None = None
    disponible: bool | None = None
    taller_id: str | None = None


def _resolve_own_taller_id(db, user) -> str | None:
    row = db.execute(
        text("SELECT id FROM emergencias.taller WHERE usuario_id = :uid"),
        {"uid": user.id},
    ).mappings().first()
    return str(row["id"]) if row else None


def _assert_tecnico_access(db, user, tecnico_id: str) -> dict:
    row = db.execute(
        text("SELECT id, taller_id, usuario_id FROM emergencias.tecnico WHERE id = :id"),
        {"id": tecnico_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Técnico no encontrado")
    if user.rol == "TALLER":
        own = db.execute(
            text(
                """SELECT 1 FROM emergencias.taller
                WHERE id = :tl AND usuario_id = :uid"""
            ),
            {"tl": row["taller_id"], "uid": user.id},
        ).first()
        if not own:
            raise HTTPException(403, "Sin permiso para este técnico")
    return dict(row)


def _assert_email_disponible(
    db, tenant_id: str, email: str, exclude_usuario_id: str | None = None
) -> None:
    sql = """SELECT id FROM emergencias.usuario
             WHERE tenant_id = :t AND lower(email) = lower(:e)"""
    params: dict = {"t": tenant_id, "e": email}
    if exclude_usuario_id:
        sql += " AND id <> :uid"
        params["uid"] = exclude_usuario_id
    if db.execute(text(sql), params).first():
        raise HTTPException(409, "El correo ya está registrado en este tenant")


def _create_tecnico_usuario(
    db,
    tenant_id: str,
    nombre: str,
    email: str,
    telefono: str | None,
    password: str | None,
) -> tuple[str, str, bool]:
    """Crea usuario TECNICO. Devuelve (usuario_id, password_usada, es_temporal)."""
    temp = password is None
    plain = password or secrets.token_urlsafe(12)
    uid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, telefono, password_hash,
             email_verificado, must_change_password)
            VALUES (:id, :t, 'TECNICO', :n, :e, :tel, :ph, true, :mcp)"""
        ),
        {
            "id": uid,
            "t": tenant_id,
            "n": nombre,
            "e": email.lower(),
            "tel": telefono,
            "ph": hash_password(plain),
            "mcp": temp,
        },
    )
    return uid, plain, temp


def _resolve_especialidad_labels(db, esp_ids: list[str], taller_id: str) -> list[str]:
    if not esp_ids:
        return []
    rows = db.execute(
        text(
            """SELECT id, nombre FROM emergencias.especialidad_taller
            WHERE taller_id = :tl AND activo = true"""
        ),
        {"tl": taller_id},
    ).mappings().all()
    found = {str(r["id"]): r["nombre"] for r in rows}
    missing = [eid for eid in esp_ids if eid not in found]
    if missing:
        raise HTTPException(400, "Una o más especialidades no pertenecen al taller")
    return [found[eid] for eid in esp_ids]


def _sync_tecnico_especialidades(db, tecnico_id: str, taller_id: str, esp_ids: list[str]) -> str | None:
    labels = _resolve_especialidad_labels(db, esp_ids, taller_id)
    db.execute(
        text("DELETE FROM emergencias.tecnico_especialidad WHERE tecnico_id = :tid"),
        {"tid": tecnico_id},
    )
    for eid in esp_ids:
        db.execute(
            text(
                """INSERT INTO emergencias.tecnico_especialidad (tecnico_id, especialidad_id)
                VALUES (:t, :e)"""
            ),
            {"t": tecnico_id, "e": eid},
        )
    return _especialidad_summary(labels)


def _enrich_tecnicos(db, rows) -> list[dict]:
    items = []
    for row in rows:
        item = dict(row)
        esp_rows = db.execute(
            text(
                """SELECT e.id, e.nombre
                FROM emergencias.tecnico_especialidad te
                JOIN emergencias.especialidad_taller e ON e.id = te.especialidad_id
                WHERE te.tecnico_id = :tid
                ORDER BY e.nombre"""
            ),
            {"tid": item["id"]},
        ).mappings().all()
        item["especialidad_ids"] = [str(r["id"]) for r in esp_rows]
        item["especialidades"] = [r["nombre"] for r in esp_rows]
        if esp_rows and not item.get("especialidad"):
            item["especialidad"] = _especialidad_summary(item["especialidades"])
        items.append(item)
    return items


@router.get("")
def list_tecnicos(
    limit: int = 100,
    offset: int = 0,
    taller_id: str | None = None,
    tupla=Depends(require_permission("tecnico", "leer")),
):
    user, perm, db = tupla
    sql = """SELECT t.*, tl.nombre AS taller_nombre, u.email AS usuario_email
             FROM emergencias.tecnico t
             JOIN emergencias.taller tl ON tl.id = t.taller_id
             LEFT JOIN emergencias.usuario u ON u.id = t.usuario_id
             WHERE 1=1"""
    params: dict = {"limit": limit, "offset": offset}
    if user.rol == "TALLER":
        own_tl = _resolve_own_taller_id(db, user)
        if not own_tl:
            return {"items": [], "total": 0}
        sql += " AND t.taller_id = :own_tl"
        params["own_tl"] = own_tl
    elif taller_id:
        sql += " AND t.taller_id = :tl"
        params["tl"] = taller_id
    sql += " ORDER BY t.nombre LIMIT :limit OFFSET :offset"
    rows = db.execute(text(sql), params).mappings().all()
    items = perm.filter_list("tecnico", _enrich_tecnicos(db, rows))
    count_sql = """SELECT count(*) AS n FROM emergencias.tecnico t WHERE 1=1"""
    count_params: dict = {}
    if user.rol == "TALLER":
        own_tl = params.get("own_tl")
        if own_tl:
            count_sql += " AND t.taller_id = :own_tl"
            count_params["own_tl"] = own_tl
    elif taller_id:
        count_sql += " AND t.taller_id = :tl"
        count_params["tl"] = taller_id
    total = db.execute(text(count_sql), count_params).scalar() or 0
    return {"items": items, "total": int(total)}


@router.get("/{tecnico_id}")
def get_tecnico(
    tecnico_id: str,
    tupla=Depends(require_permission("tecnico", "leer")),
):
    user, perm, db = tupla
    _assert_tecnico_access(db, user, tecnico_id)
    row = db.execute(
        text(
            """SELECT t.*, tl.nombre AS taller_nombre, u.email AS usuario_email
               FROM emergencias.tecnico t
               JOIN emergencias.taller tl ON tl.id = t.taller_id
               LEFT JOIN emergencias.usuario u ON u.id = t.usuario_id
               WHERE t.id = :id"""
        ),
        {"id": tecnico_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Técnico no encontrado")
    items = _enrich_tecnicos(db, [row])
    return perm.filter_dict("tecnico", items[0])


@router.post("", status_code=201)
def create_tecnico(
    body: TecnicoIn,
    tupla=Depends(require_permission("tecnico", "crear")),
):
    user, perm, db = tupla
    assert_tenant_can_create_technician(db, user.tenant)
    if user.rol == "TALLER":
        own = db.execute(
            text(
                """SELECT id FROM emergencias.taller
                WHERE id = :tl AND usuario_id = :uid"""
            ),
            {"tl": body.taller_id, "uid": user.id},
        ).first()
        if not own:
            raise HTTPException(403, "Solo puede registrar técnicos en su taller")

    tid = str(uuid.uuid4())
    esp_summary = body.especialidad
    if body.especialidad_ids:
        labels = _resolve_especialidad_labels(db, body.especialidad_ids, body.taller_id)
        esp_summary = _especialidad_summary(labels)

    _assert_email_disponible(db, user.tenant, body.email)
    uid, plain_pwd, es_temporal = _create_tecnico_usuario(
        db, user.tenant, body.nombre, body.email, body.telefono, body.password
    )

    db.execute(
        text(
            """INSERT INTO emergencias.tecnico
            (id, tenant_id, taller_id, usuario_id, nombre, telefono, especialidad)
            VALUES (:id, :t, :tl, :u, :n, :tel, :esp)"""
        ),
        {
            "id": tid,
            "t": user.tenant,
            "tl": body.taller_id,
            "u": uid,
            "n": body.nombre,
            "tel": body.telefono,
            "esp": esp_summary,
        },
    )
    if body.especialidad_ids:
        _sync_tecnico_especialidades(db, tid, body.taller_id, body.especialidad_ids)

    if es_temporal:
        send_temp_password_email(body.email.lower(), body.nombre, plain_pwd)

    result: dict = {"id": tid, "usuario_id": uid}
    if es_temporal:
        result["password_temporal"] = plain_pwd
    return result


@router.patch("/{tecnico_id}")
def update_tecnico(
    tecnico_id: str,
    body: TecnicoUpdate,
    tupla=Depends(require_permission("tecnico", "actualizar")),
):
    user, perm, db = tupla
    row = _assert_tecnico_access(db, user, tecnico_id)

    if body.taller_id is not None and user.rol == "TALLER":
        raise HTTPException(403, "No puede reasignar técnicos a otro taller")

    sets: list[str] = []
    params: dict = {"id": tecnico_id}
    if body.nombre is not None:
        sets.append("nombre = :n")
        params["n"] = body.nombre
    if body.telefono is not None:
        sets.append("telefono = :tel")
        params["tel"] = body.telefono
    if body.disponible is not None:
        sets.append("disponible = :d")
        params["d"] = body.disponible
    if body.taller_id is not None:
        taller_ok = db.execute(
            text("SELECT id FROM emergencias.taller WHERE id = :id"),
            {"id": body.taller_id},
        ).first()
        if not taller_ok:
            raise HTTPException(400, "Taller destino no válido")
        sets.append("taller_id = :tl")
        params["tl"] = body.taller_id
    if body.especialidad_ids is not None:
        target_taller = body.taller_id or str(row["taller_id"])
        esp_summary = _sync_tecnico_especialidades(
            db, tecnico_id, target_taller, body.especialidad_ids
        )
        sets.append("especialidad = :esp")
        params["esp"] = esp_summary

    usuario_id = str(row["usuario_id"]) if row.get("usuario_id") else None

    if body.email is not None or body.password is not None:
        if body.email is not None:
            if usuario_id:
                _assert_email_disponible(db, user.tenant, body.email, usuario_id)
                db.execute(
                    text(
                        """UPDATE emergencias.usuario SET email = :e
                        WHERE id = :uid AND tenant_id = :t"""
                    ),
                    {"e": body.email.lower(), "uid": usuario_id, "t": user.tenant},
                )
            else:
                _assert_email_disponible(db, user.tenant, body.email)
                nombre = body.nombre or db.execute(
                    text("SELECT nombre FROM emergencias.tecnico WHERE id = :id"),
                    {"id": tecnico_id},
                ).scalar()
                uid, _, _ = _create_tecnico_usuario(
                    db,
                    user.tenant,
                    str(nombre),
                    body.email,
                    body.telefono,
                    body.password,
                )
                sets.append("usuario_id = :uid")
                params["uid"] = uid
                usuario_id = uid

        if body.password is not None:
            if not usuario_id:
                raise HTTPException(
                    400, "Debe indicar un correo para asignar contraseña al técnico"
                )
            db.execute(
                text(
                    """UPDATE emergencias.usuario
                    SET password_hash = :ph, must_change_password = false
                    WHERE id = :uid"""
                ),
                {"ph": hash_password(body.password), "uid": usuario_id},
            )
        if body.nombre is not None and usuario_id:
            db.execute(
                text("UPDATE emergencias.usuario SET nombre = :n WHERE id = :uid"),
                {"n": body.nombre, "uid": usuario_id},
            )
        if body.telefono is not None and usuario_id:
            db.execute(
                text("UPDATE emergencias.usuario SET telefono = :tel WHERE id = :uid"),
                {"tel": body.telefono, "uid": usuario_id},
            )

    if sets:
        sql = f"UPDATE emergencias.tecnico SET {', '.join(sets)} WHERE id = :id"
        db.execute(text(sql), params)

    return {"ok": True}


@router.delete("/{tecnico_id}")
def delete_tecnico(
    tecnico_id: str,
    tupla=Depends(require_permission("tecnico", "eliminar")),
):
    user, perm, db = tupla
    _assert_tecnico_access(db, user, tecnico_id)
    active = db.execute(
        text(
            """SELECT count(*) FROM emergencias.asignacion
            WHERE tecnico_id = :id AND estado IN ('ASIGNADO', 'ACEPTADO')"""
        ),
        {"id": tecnico_id},
    ).scalar()
    if active and int(active) > 0:
        db.execute(
            text("UPDATE emergencias.tecnico SET disponible = FALSE WHERE id = :id"),
            {"id": tecnico_id},
        )
        return {"ok": True, "desactivado": True}
    db.execute(
        text("DELETE FROM emergencias.tecnico WHERE id = :id"),
        {"id": tecnico_id},
    )
    return {"ok": True}
