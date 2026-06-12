import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from ..core.config import settings
from ..core.deps import CurrentUser, get_current_user, get_current_user_verified, get_db, get_db_public
from ..core.helpers import audit
from ..core.security import create_access_token, hash_password, verify_password
from ..schemas.auth import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    LoginOut,
    RegisterIn,
    ResetPasswordIn,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
def register(body: RegisterIn, db=Depends(get_db_public)):
    existing = db.execute(
        text(
            """SELECT id FROM emergencias.usuario
            WHERE tenant_id = :t AND email = :e"""
        ),
        {"t": settings.public_tenant_id, "e": body.email.lower()},
    ).first()
    if existing:
        raise HTTPException(409, "Email already registered")

    uid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, telefono, password_hash, email_verificado)
            VALUES (:id, :t, 'CONDUCTOR', :n, :e, :tel, :ph, true)"""
        ),
        {
            "id": uid,
            "t": settings.public_tenant_id,
            "n": body.nombre,
            "e": body.email.lower(),
            "tel": body.telefono,
            "ph": hash_password(body.password),
        },
    )
    return {"id": uid}


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db=Depends(get_db_public)):
    params = {"e": body.email.lower()}
    sql = """SELECT id, tenant_id, rol, password_hash, activo, must_change_password
             FROM emergencias.usuario WHERE email = :e"""
    if body.tenant_id:
        sql += " AND tenant_id = :t"
        params["t"] = str(body.tenant_id)
    user = db.execute(text(sql), params).mappings().first()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user["activo"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user disabled")

    jti = str(uuid.uuid4())
    must_change = user.get("must_change_password", False)
    token = create_access_token(
        sub=str(user["id"]),
        rol=user["rol"],
        tenant=str(user["tenant_id"]) if user["tenant_id"] else None,
        jti=jti,
        must_change_password=must_change,
    )
    db.execute(
        text("UPDATE emergencias.usuario SET ultimo_acceso = now() WHERE id = :id"),
        {"id": str(user["id"])},
    )
    audit(
        db,
        tenant_id=str(user["tenant_id"]) if user["tenant_id"] else None,
        usuario_id=str(user["id"]),
        accion="LOGIN",
        entidad="usuario",
        entidad_id=str(user["id"]),
    )
    return LoginOut(
        access_token=token,
        rol=user["rol"],
        tenant_id=str(user["tenant_id"]) if user["tenant_id"] else None,
        usuario_id=str(user["id"]),
        must_change_password=must_change,
    )


@router.post("/logout", status_code=204)
def logout(user: CurrentUser = Depends(get_current_user), db=Depends(get_db)):
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    db.execute(
        text(
            """INSERT INTO emergencias.token_revocado (jti, usuario_id, expira_en)
            VALUES (:j, :u, :exp) ON CONFLICT (jti) DO NOTHING"""
        ),
        {"j": user.jti, "u": user.id, "exp": exp},
    )
    audit(
        db,
        tenant_id=user.tenant,
        usuario_id=user.id,
        accion="LOGOUT",
        entidad="usuario",
        entidad_id=user.id,
    )


@router.post("/forgot-password", status_code=202)
def forgot_password(body: ForgotPasswordIn, db=Depends(get_db_public)):
    user = db.execute(
        text("SELECT id FROM emergencias.usuario WHERE email = :e"),
        {"e": body.email.lower()},
    ).first()
    if user:
        tok = str(uuid.uuid4())
        db.execute(
            text(
                """INSERT INTO emergencias.token_recuperacion
                (usuario_id, token_hash, expira_en)
                VALUES (:u, :th, :exp)"""
            ),
            {
                "u": str(user[0]),
                "th": hash_password(tok),
                "exp": datetime.now(timezone.utc) + timedelta(hours=24),
            },
        )
    return {"detail": "If the email exists, a reset link was sent"}


@router.post("/reset-password", status_code=204)
def reset_password(body: ResetPasswordIn, db=Depends(get_db_public)):
    rows = db.execute(
        text(
            """SELECT tr.id, tr.usuario_id, tr.token_hash
            FROM emergencias.token_recuperacion tr
            WHERE tr.usado = false AND tr.expira_en > now()"""
        )
    ).mappings().all()
    matched = None
    for r in rows:
        if verify_password(body.token, r["token_hash"]):
            matched = r
            break
    if not matched:
        raise HTTPException(400, "Invalid or expired token")
    db.execute(
        text("UPDATE emergencias.usuario SET password_hash = :ph WHERE id = :u"),
        {"ph": hash_password(body.new_password), "u": str(matched["usuario_id"])},
    )
    db.execute(
        text("UPDATE emergencias.token_recuperacion SET usado = true WHERE id = :id"),
        {"id": matched["id"]},
    )


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordIn,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    row = db.execute(
        text("SELECT password_hash FROM emergencias.usuario WHERE id = :id"),
        {"id": user.id},
    ).mappings().first()
    if not row or not verify_password(body.password_actual, row["password_hash"]):
        raise HTTPException(401, "Contraseña actual incorrecta")
    if len(body.password_nueva) < 8:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 8 caracteres")
    db.execute(
        text(
            """UPDATE emergencias.usuario
            SET password_hash = :ph, must_change_password = FALSE
            WHERE id = :id"""
        ),
        {"ph": hash_password(body.password_nueva), "id": user.id},
    )
    audit(
        db,
        tenant_id=user.tenant,
        usuario_id=user.id,
        accion="CHANGE_PASSWORD",
        entidad="usuario",
        entidad_id=user.id,
    )
