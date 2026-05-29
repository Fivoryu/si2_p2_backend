import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.deps import get_db, require_roles
from ..core.security import hash_password

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


@router.post("", status_code=201)
def create_tenant(
    body: TenantCreate,
    user=Depends(require_roles("ADMIN_PLATAFORMA")),
    db=Depends(get_db),
):
    tid = str(uuid.uuid4())
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
    user=Depends(require_roles("ADMIN_PLATAFORMA")),
    db=Depends(get_db),
):
    uid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, password_hash, email_verificado)
            VALUES (:id, :t, 'ADMIN_TENANT', :n, :e, :ph, true)"""
        ),
        {
            "id": uid,
            "t": tenant_id,
            "n": body.nombre,
            "e": body.email.lower(),
            "ph": hash_password("password123"),
        },
    )
    return {"usuario_id": uid}


@router.patch("/{tenant_id}/plan")
def patch_plan(
    tenant_id: str,
    body: PlanPatch,
    user=Depends(require_roles("ADMIN_PLATAFORMA")),
    db=Depends(get_db),
):
    db.execute(
        text("UPDATE emergencias.tenant SET plan_id = :p WHERE id = :id"),
        {"p": body.plan_id, "id": tenant_id},
    )
    return {"ok": True}
