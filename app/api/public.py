import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from ..core.deps import get_db_public
from ..core.security import hash_password
from ..schemas.public import PlanOut, TenantSignupIn

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/plans", response_model=list[PlanOut])
def list_plans(db=Depends(get_db_public)):
    rows = db.execute(
        text(
            """SELECT id, nombre, max_talleres, max_tecnicos, ia_avanzada, precio_mensual
            FROM emergencias.plan
            ORDER BY precio_mensual ASC"""
        )
    ).mappings().all()
    return [
        PlanOut(
            id=str(r["id"]),
            nombre=r["nombre"],
            max_talleres=r["max_talleres"],
            max_tecnicos=r["max_tecnicos"],
            ia_avanzada=r["ia_avanzada"],
            precio_mensual=float(r["precio_mensual"]),
        )
        for r in rows
    ]


@router.post("/signup", status_code=201)
def signup_tenant(body: TenantSignupIn, db=Depends(get_db_public)):
    plan = db.execute(
        text("SELECT id FROM emergencias.plan WHERE id = :id"),
        {"id": body.plan_id},
    ).first()
    if not plan:
        raise HTTPException(400, "Plan no válido")

    email = body.admin_email.lower()
    existing = db.execute(
        text("SELECT id FROM emergencias.usuario WHERE email = :e LIMIT 1"),
        {"e": email},
    ).first()
    if existing:
        raise HTTPException(409, "El correo ya está registrado")

    tid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    dominio = (body.dominio or "").strip() or None

    db.execute(
        text(
            """INSERT INTO emergencias.tenant (id, nombre, dominio, plan_id)
            VALUES (:id, :n, :d, :p)"""
        ),
        {"id": tid, "n": body.nombre_organizacion.strip(), "d": dominio, "p": body.plan_id},
    )
    db.execute(
        text(
            """INSERT INTO emergencias.usuario
            (id, tenant_id, rol, nombre, email, telefono, password_hash, email_verificado)
            VALUES (:id, :t, 'ADMIN_TENANT', :n, :e, :tel, :ph, true)"""
        ),
        {
            "id": uid,
            "t": tid,
            "n": body.admin_nombre.strip(),
            "e": email,
            "tel": body.admin_telefono,
            "ph": hash_password(body.password),
        },
    )
    return {
        "tenant_id": tid,
        "usuario_id": uid,
        "mensaje": "Cuenta creada. Inicie sesión con su correo y contraseña.",
    }
