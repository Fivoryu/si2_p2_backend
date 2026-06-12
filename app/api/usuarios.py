from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user_verified, get_db, require_roles, SessionLocal
from ..core.security import hash_password

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


class ProfilePatch(BaseModel):
    nombre: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    password: str | None = None


class FcmTokenIn(BaseModel):
    fcm_token: str


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user_verified), db=Depends(get_db)):
    row = db.execute(
        text(
            """SELECT id, tenant_id, rol, nombre, email, telefono, email_verificado, activo
            FROM emergencias.usuario WHERE id = :id"""
        ),
        {"id": user.id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)


@router.patch("/me")
def patch_me(
    body: ProfilePatch,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    updates = []
    params = {"id": user.id}
    if body.nombre:
        updates.append("nombre = :nombre")
        params["nombre"] = body.nombre
    if body.telefono is not None:
        updates.append("telefono = :telefono")
        params["telefono"] = body.telefono
    if body.email:
        updates.append("email = :email")
        params["email"] = body.email.lower()
    if body.password:
        updates.append("password_hash = :ph")
        params["ph"] = hash_password(body.password)
    if not updates:
        return me(user, db)
    db.execute(
        text(f"UPDATE emergencias.usuario SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    return me(user, db)


@router.post("/me/fcm")
def set_fcm(
    body: FcmTokenIn,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    db.execute(
        text("UPDATE emergencias.usuario SET fcm_token = :t WHERE id = :id"),
        {"t": body.fcm_token, "id": user.id},
    )
    return {"ok": True}


@router.get("/me/notificaciones")
def mis_notificaciones(
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    rows = db.execute(
        text(
            """SELECT n.id, n.titulo, n.mensaje, n.canal, n.incidente_id,
                      n.enviada, n.created_at
               FROM emergencias.notificacion n
               WHERE n.usuario_id = :uid
               ORDER BY n.created_at DESC
               LIMIT 50"""
        ),
        {"uid": user.id},
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/me/permisos")
def mis_permisos(user: CurrentUser = Depends(get_current_user_verified)):
    """Devuelve la matriz completa de permisos del usuario (RBAC)."""
    from ..services.permissions import PermissionService

    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": user.tenant or ""},
        )
        svc = PermissionService(db, user)
        svc._load()
        return {
            "usuario_id": user.id,
            "rol_base": user.rol,
            **svc.get_full_permissions(),
        }
    finally:
        db.close()


@router.get("")
def list_usuarios(
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "ADMIN_PLATAFORMA")),
    db=Depends(get_db),
):
    """Lista usuarios del tenant (para asignación de roles)."""
    if user.rol == "ADMIN_PLATAFORMA":
        rows = db.execute(
            text("SELECT id, nombre, email FROM emergencias.usuario WHERE activo = TRUE ORDER BY nombre")
        ).mappings().all()
    else:
        rows = db.execute(
            text("SELECT id, nombre, email FROM emergencias.usuario WHERE tenant_id = :tid AND activo = TRUE ORDER BY nombre"),
            {"tid": user.tenant},
        ).mappings().all()
    return [dict(r) for r in rows]
