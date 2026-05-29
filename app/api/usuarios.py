from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user, get_db, require_roles
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
def me(user: CurrentUser = Depends(get_current_user), db=Depends(get_db)):
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
    user: CurrentUser = Depends(get_current_user),
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
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    db.execute(
        text("UPDATE emergencias.usuario SET fcm_token = :t WHERE id = :id"),
        {"t": body.fcm_token, "id": user.id},
    )
    return {"ok": True}
