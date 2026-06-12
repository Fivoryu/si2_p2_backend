import uuid
import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import require_permission
from ..core.security import hash_password
from ..services.email import send_temp_password_email

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    nombre: str
    dominio: str | None = None
    plan_id: str


class TenantAdminIn(BaseModel):
    email: EmailStr
    nombre: str


class PlanPatch(BaseModel):
    plan_id: str


@router.get("")
def list_tenants(
    tupla=Depends(require_permission("tenant", "leer")),
):
    user, perm, db = tupla
    rows = db.execute(
        text("SELECT id, nombre, dominio, plan_id FROM emergencias.tenant ORDER BY nombre")
    ).mappings().all()
    return {"items": perm.filter_list("tenant", [dict(r) for r in rows])}


@router.get("/planes")
def list_plans(
    tupla=Depends(require_permission("plan", "leer")),
):
    user, perm, db = tupla
    rows = db.execute(
        text("SELECT id, nombre FROM emergencias.plan ORDER BY nombre")
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("", status_code=201)
def create_tenant(
    body: TenantCreate,
    tupla=Depends(require_permission("tenant", "crear")),
):
    user, perm, db = tupla
    tid = str(uuid.uuid4())
    temp_password = secrets.token_urlsafe(12)
    db.execute(
        text(
            """INSERT INTO emergencias.tenant (id, nombre, dominio, plan_id)
            VALUES (:id, :n, :d, :p)"""
        ),
        {"id": tid, "n": body.nombre, "d": body.dominio, "p": body.plan_id},
    )
    return {"id": tid}


@router.post("/{tenant_id}/admin", status_code=201)
def assign_admin(
    tenant_id: str,
    body: TenantAdminIn,
    tupla=Depends(require_permission("usuario", "crear")),
):
    user, perm, db = tupla
    uid = str(uuid.uuid4())
    temp_password = secrets.token_urlsafe(12)
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, password_hash, email_verificado, must_change_password)
            VALUES (:id, :t, 'ADMIN_TENANT', :n, :e, :ph, true, true)"""
        ),
        {
            "id": uid,
            "t": tenant_id,
            "n": body.nombre,
            "e": body.email.lower(),
            "ph": hash_password(temp_password),
        },
    )
    send_temp_password_email(body.email.lower(), body.nombre, temp_password)
    return {"usuario_id": uid, "password_temporal": temp_password}


@router.patch("/{tenant_id}/plan")
def patch_plan(
    tenant_id: str,
    body: PlanPatch,
    tupla=Depends(require_permission("tenant", "actualizar")),
):
    user, perm, db = tupla
    db.execute(
        text("UPDATE emergencias.tenant SET plan_id = :p WHERE id = :id"),
        {"p": body.plan_id, "id": tenant_id},
    )
    return {"ok": True}
