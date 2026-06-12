"""CRUD de roles, permisos y asignación usuario↔rol (RBAC)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user_verified, require_permission, SessionLocal

router = APIRouter(prefix="/roles", tags=["Roles (RBAC)"])


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------
class RolCreate(BaseModel):
    nombre: str
    descripcion: str | None = None


class RolUpdate(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    activo: bool | None = None


class PermisoEntidadItem(BaseModel):
    entidad: str
    puede_crear: bool = False
    puede_leer: bool = False
    puede_actualizar: bool = False
    puede_eliminar: bool = False


class PermisoColumnaItem(BaseModel):
    entidad: str
    columna: str
    puede_ver: bool = True
    puede_editar: bool = False


class PermisosEntidadUpdate(BaseModel):
    permisos: list[PermisoEntidadItem]


class PermisosColumnaUpdate(BaseModel):
    permisos: list[PermisoColumnaItem]


class AsignarRol(BaseModel):
    usuario_id: str


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _get_rol(db, rol_id: str, tenant_id: str | None) -> dict | None:
    row = db.execute(
        text("""
            SELECT id, tenant_id, nombre, descripcion, es_base, base_rol, activo
            FROM emergencias.rol WHERE id = :id
        """),
        {"id": rol_id},
    ).mappings().first()
    if row is None:
        return None
    return dict(row)


def _assert_rol_access(rol: dict, user: CurrentUser):
    if user.rol == "ADMIN_PLATAFORMA":
        return
    if str(rol["tenant_id"]) != str(user.tenant):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Rol de otro tenant")
    if rol["es_base"] and rol["base_rol"] != user.rol:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes modificar roles base ajenos")


def _assert_admin(user: CurrentUser):
    if user.rol not in ("ADMIN_PLATAFORMA", "ADMIN_TENANT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo admin puede gestionar roles")


# ------------------------------------------------------------------
# CRUD básico
# ------------------------------------------------------------------
@router.get("")
async def listar_roles(
    tupla=Depends(require_permission("rol", "leer")),
):
    user, _, _ = tupla
    db = SessionLocal()
    try:
        if user.rol == "ADMIN_PLATAFORMA":
            rows = db.execute(
                text("SELECT id, tenant_id, nombre, descripcion, es_base, base_rol, activo FROM emergencias.rol ORDER BY nombre")
            ).mappings().all()
        else:
            rows = db.execute(
                text("""
                    SELECT id, tenant_id, nombre, descripcion, es_base, base_rol, activo
                    FROM emergencias.rol WHERE tenant_id = :tid ORDER BY nombre
                """),
                {"tid": user.tenant},
            ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("", status_code=status.HTTP_201_CREATED)
async def crear_rol(
    body: RolCreate,
    tupla=Depends(require_permission("rol", "crear")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                INSERT INTO emergencias.rol (tenant_id, nombre, descripcion, es_base)
                VALUES (:tid, :n, :d, FALSE)
                RETURNING id, tenant_id, nombre, descripcion, es_base, base_rol, activo
            """),
            {"tid": user.tenant, "n": body.nombre, "d": body.descripcion},
        ).mappings().first()
        db.commit()
        return dict(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/{rol_id}")
async def ver_rol(
    rol_id: str,
    tupla=Depends(require_permission("rol", "leer")),
):
    user, _, _ = tupla
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")

        permisos_entidad = db.execute(
            text("""
                SELECT entidad, puede_crear, puede_leer, puede_actualizar, puede_eliminar
                FROM emergencias.rol_permiso_entidad WHERE rol_id = :rid
            """),
            {"rid": rol_id},
        ).mappings().all()

        permisos_columna = db.execute(
            text("""
                SELECT entidad, columna, puede_ver, puede_editar
                FROM emergencias.rol_permiso_columna WHERE rol_id = :rid
            """),
            {"rid": rol_id},
        ).mappings().all()

        usuarios_con_rol = db.execute(
            text("""
                SELECT u.id, u.nombre, u.email
                FROM emergencias.usuario_rol ur
                JOIN emergencias.usuario u ON u.id = ur.usuario_id
                WHERE ur.rol_id = :rid
            """),
            {"rid": rol_id},
        ).mappings().all()

        return {
            **rol,
            "permisos_entidad": [dict(p) for p in permisos_entidad],
            "permisos_columna": [dict(p) for p in permisos_columna],
            "usuarios": [dict(u) for u in usuarios_con_rol],
        }
    finally:
        db.close()


@router.patch("/{rol_id}")
async def editar_rol(
    rol_id: str,
    body: RolUpdate,
    tupla=Depends(require_permission("rol", "actualizar")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        updates = []
        params: dict = {"id": rol_id}
        if body.nombre is not None:
            updates.append("nombre = :n")
            params["n"] = body.nombre
        if body.descripcion is not None:
            updates.append("descripcion = :d")
            params["d"] = body.descripcion
        if body.activo is not None:
            updates.append("activo = :a")
            params["a"] = body.activo

        if not updates:
            return rol

        sql = f"UPDATE emergencias.rol SET {', '.join(updates)} WHERE id = :id RETURNING id, tenant_id, nombre, descripcion, es_base, base_rol, activo"
        row = db.execute(text(sql), params).mappings().first()
        db.commit()
        return dict(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{rol_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_rol(
    rol_id: str,
    tupla=Depends(require_permission("rol", "eliminar")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        if rol["es_base"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No se puede eliminar un rol base")

        db.execute(text("DELETE FROM emergencias.rol WHERE id = :id"), {"id": rol_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ------------------------------------------------------------------
# Permisos por entidad
# ------------------------------------------------------------------
@router.patch("/{rol_id}/permisos-entidad")
async def actualizar_permisos_entidad(
    rol_id: str,
    body: PermisosEntidadUpdate,
    tupla=Depends(require_permission("rol_permiso_entidad", "actualizar")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        # ADMIN_TENANT no puede otorgar permisos que no tiene
        if user.rol == "ADMIN_TENANT":
            mis_perm = db.execute(
                text("""
                    SELECT entidad,
                           bool_or(puede_crear) AS c, bool_or(puede_leer) AS r,
                           bool_or(puede_actualizar) AS u, bool_or(puede_eliminar) AS d
                    FROM emergencias.rol_permiso_entidad re
                    JOIN emergencias.rol r ON r.id = re.rol_id
                    WHERE r.tenant_id = :tid AND r.es_base = TRUE AND r.base_rol = :base
                    GROUP BY entidad
                """),
                {"tid": user.tenant, "base": user.rol},
            ).mappings().all()
            mis = {p["entidad"]: p for p in mis_perm}
            for p in body.permisos:
                mp = mis.get(p.entidad)
                if not mp:
                    raise HTTPException(status.HTTP_403_FORBIDDEN,
                        f"No tienes permisos sobre '{p.entidad}'")
                if (p.puede_crear and not mp["c"]) or \
                   (p.puede_leer and not mp["r"]) or \
                   (p.puede_actualizar and not mp["u"]) or \
                   (p.puede_eliminar and not mp["d"]):
                    raise HTTPException(status.HTTP_403_FORBIDDEN,
                        f"No puedes otorgar permisos que no posees en '{p.entidad}'")

        # Eliminar existentes y reinsertar
        db.execute(text("DELETE FROM emergencias.rol_permiso_entidad WHERE rol_id = :rid"), {"rid": rol_id})
        for p in body.permisos:
            db.execute(
                text("""
                    INSERT INTO emergencias.rol_permiso_entidad
                        (rol_id, entidad, puede_crear, puede_leer, puede_actualizar, puede_eliminar)
                    VALUES (:rid, :e, :c, :r, :u, :d)
                """),
                {"rid": rol_id, "e": p.entidad, "c": p.puede_crear, "r": p.puede_leer,
                 "u": p.puede_actualizar, "d": p.puede_eliminar},
            )
        db.commit()
        return {"ok": True, "permisos": len(body.permisos)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ------------------------------------------------------------------
# Permisos por columna
# ------------------------------------------------------------------
@router.patch("/{rol_id}/permisos-columnas")
async def actualizar_permisos_columnas(
    rol_id: str,
    body: PermisosColumnaUpdate,
    tupla=Depends(require_permission("rol_permiso_columna", "actualizar")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        db.execute(text("DELETE FROM emergencias.rol_permiso_columna WHERE rol_id = :rid"), {"rid": rol_id})
        for p in body.permisos:
            db.execute(
                text("""
                    INSERT INTO emergencias.rol_permiso_columna
                        (rol_id, entidad, columna, puede_ver, puede_editar)
                    VALUES (:rid, :e, :col, :v, :e2)
                """),
                {"rid": rol_id, "e": p.entidad, "col": p.columna,
                 "v": p.puede_ver, "e2": p.puede_editar},
            )
        db.commit()
        return {"ok": True, "permisos": len(body.permisos)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ------------------------------------------------------------------
# Asignación usuario ↔ rol
# ------------------------------------------------------------------
@router.post("/usuarios/{usuario_id}/roles", status_code=status.HTTP_201_CREATED)
async def asignar_rol(
    usuario_id: str,
    body: AsignarRol,
    tupla=Depends(require_permission("usuario_rol", "crear")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, body.rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        # Verificar que el usuario pertenece al mismo tenant
        u_row = db.execute(
            text("SELECT id, tenant_id FROM emergencias.usuario WHERE id = :uid"),
            {"uid": usuario_id},
        ).mappings().first()
        if not u_row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
        if user.rol != "ADMIN_PLATAFORMA" and u_row["tenant_id"] != user.tenant:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario de otro tenant")

        db.execute(
            text("""
                INSERT INTO emergencias.usuario_rol (usuario_id, rol_id, asignado_por)
                VALUES (:uid, :rid, :ap)
                ON CONFLICT (usuario_id, rol_id) DO NOTHING
            """),
            {"uid": usuario_id, "rid": body.rol_id, "ap": user.id},
        )
        db.commit()
        return {"ok": True}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/usuarios/{usuario_id}/roles/{rol_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_rol(
    usuario_id: str,
    rol_id: str,
    tupla=Depends(require_permission("usuario_rol", "eliminar")),
):
    user, _, _ = tupla
    _assert_admin(user)
    db = SessionLocal()
    try:
        rol = _get_rol(db, rol_id, user.tenant)
        if not rol:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado")
        _assert_rol_access(rol, user)

        db.execute(
            text("DELETE FROM emergencias.usuario_rol WHERE usuario_id = :uid AND rol_id = :rid"),
            {"uid": usuario_id, "rid": rol_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
