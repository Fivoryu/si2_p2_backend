import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ..core.config import settings
from ..core.deps import get_db_public
from ..core.helpers import audit
from ..core.security import hash_password
from ..services.email import send_temp_password_email

router = APIRouter(prefix="/pagos", tags=["pagos"])


class PlanCheckoutIn(BaseModel):
    plan_id: str
    admin_email: EmailStr
    admin_nombre: str
    org_nombre: str
    dominio: str | None = None


def _get_redis():
    """Get Redis client from app state or create inline."""
    from redis import Redis

    return Redis.from_url(settings.redis_url, decode_responses=True)


@router.post("/plan-checkout")
def plan_checkout(body: PlanCheckoutIn, db=Depends(get_db_public)):
    # 1. Validate plan exists and is not free
    plan = db.execute(
        text(
            "SELECT id, nombre, precio_mensual FROM emergencias.plan WHERE id = :id"
        ),
        {"id": body.plan_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "Plan no encontrado")
    if float(plan["precio_mensual"]) <= 0:
        raise HTTPException(
            400, "Este plan es gratuito, no requiere pago. Use /public/signup"
        )

    # 2. Create invoice in AcquireMock
    invoice_id = str(uuid.uuid4())
    amount = int(float(plan["precio_mensual"]))  # 1 BOB

    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"{settings.acquiremock_url}/api/create-invoice",
                json={
                    "amount": amount,
                    "reference": f"plan_{body.plan_id}_{invoice_id}",
                    "webhookUrl": f"{settings.backend_internal_url}/pagos/plan-webhook",
                    "redirectUrl": f"{settings.web_public_url}/registro/exito",
                },
            )
            r.raise_for_status()
            page_url = r.json()["pageUrl"]
    except Exception as exc:
        raise HTTPException(502, f"Error al crear sesión de pago: {exc}") from exc

    # 3. Store pending signup in Redis (TTL 30 min)
    redis_client = _get_redis()
    signup_data = {
        "plan_id": body.plan_id,
        "admin_email": body.admin_email,
        "admin_nombre": body.admin_nombre,
        "org_nombre": body.org_nombre,
        "dominio": body.dominio or "",
    }
    redis_client.setex(
        f"plan_signup:{invoice_id}",
        timedelta(minutes=30),
        json.dumps(signup_data),
    )

    return {"pageUrl": page_url, "invoice_id": invoice_id}


@router.post("/plan-webhook")
async def plan_webhook(request: Request):
    # 1. Verify HMAC-SHA256 signature
    # AcquireMock signs: hmac.new(secret, json.dumps(data, sort_keys=True).encode(), sha256)
    signature = request.headers.get("X-Signature", "")
    raw_payload = await request.body()
    payload = json.loads(raw_payload)

    # Re-serialize with sorted keys to match AcquireMock's signing logic
    message = json.dumps(payload, sort_keys=True).encode()
    expected = hmac.new(
        settings.acquiremock_webhook_secret.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(403, "Invalid signature")

    # 2. Only process successful payments
    if payload.get("status") != "paid":
        return {"received": True}

    reference = payload.get("reference", "")
    # Extract invoice_id from reference (format: plan_{plan_id}_{invoice_id})
    parts = reference.split("_")
    invoice_id = parts[-1] if len(parts) >= 3 else reference

    # 3. Retrieve pending signup data
    redis_client = _get_redis()
    data_raw = redis_client.get(f"plan_signup:{invoice_id}")
    if not data_raw:
        raise HTTPException(404, "Signup session expired or already processed")
    data = json.loads(data_raw)

    # 4. Create tenant + admin user, or update existing user
    db = next(get_db_public())
    temp_password = secrets.token_urlsafe(12)
    tid = None
    try:
        existing_user = db.execute(
            text("SELECT id, tenant_id FROM emergencias.usuario WHERE email = :e LIMIT 1"),
            {"e": data["admin_email"].lower()},
        ).mappings().first()

        if existing_user:
            # Email already registered → update password + force change
            uid = str(existing_user["id"])
            tid = str(existing_user["tenant_id"])
            db.execute(
                text(
                    """UPDATE emergencias.usuario
                    SET password_hash = :ph, must_change_password = TRUE
                    WHERE id = :id"""
                ),
                {"ph": hash_password(temp_password), "id": uid},
            )
            db.commit()
        else:
            # New user → create tenant + admin
            tid = str(uuid.uuid4())
            uid = str(uuid.uuid4())

            db.execute(
                text(
                    """INSERT INTO emergencias.tenant (id, nombre, dominio, plan_id)
                    VALUES (:id, :n, :d, :p)"""
                ),
                {
                    "id": tid,
                    "n": data["org_nombre"],
                    "d": data["dominio"] or None,
                    "p": data["plan_id"],
                },
            )
            db.execute(
                text(
                    """INSERT INTO emergencias.usuario
                    (id, tenant_id, rol, nombre, email, password_hash,
                     email_verificado, must_change_password)
                    VALUES (:id, :t, 'ADMIN_TENANT', :n, :e, :ph, TRUE, TRUE)"""
                ),
                {
                    "id": uid,
                    "t": tid,
                    "n": data["admin_nombre"],
                    "e": data["admin_email"].lower(),
                    "ph": hash_password(temp_password),
                },
            )
            db.commit()

        # 5. Send email with temp password
        send_temp_password_email(
            data["admin_email"], data["admin_nombre"], temp_password
        )

        audit(
            db,
            tenant_id=tid,
            usuario_id=uid,
            accion="PLAN_PAYMENT_COMPLETE",
            entidad="tenant",
            entidad_id=tid,
            detalle={"plan_id": data["plan_id"]},
        )

    finally:
        db.close()

    # 6. Clean up Redis
    redis_client.delete(f"plan_signup:{invoice_id}")

    return {"status": "ok", "tenant_id": tid}
