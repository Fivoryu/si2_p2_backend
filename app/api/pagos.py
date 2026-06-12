import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..core.config import settings
from ..core.db import SessionLocalAdmin
from ..core.deps import require_permission

router = APIRouter(prefix="/pagos", tags=["pagos"])


def get_admin_db():
    db = SessionLocalAdmin()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class PaymentIntentIn(BaseModel):
    incidente_id: str
    cotizacion_id: str


def _accepted_quote_for_driver(db, body: PaymentIntentIn, user_id: str):
    cot = db.execute(
        text(
            """SELECT c.id, c.monto, c.estado, c.incidente_id, c.tenant_id,
                      i.conductor_id, i.estado AS incidente_estado
               FROM emergencias.cotizacion c
               JOIN emergencias.incidente i ON i.id = c.incidente_id
               WHERE c.id = :cid AND c.incidente_id = :iid"""
        ),
        {"cid": body.cotizacion_id, "iid": body.incidente_id},
    ).mappings().first()
    if not cot:
        raise HTTPException(404, "Cotizacion not found")
    if str(cot["conductor_id"]) != user_id:
        raise HTTPException(403, "No puedes pagar esta cotizacion")
    if cot["estado"] != "ACEPTADA":
        raise HTTPException(409, "La cotizacion debe estar aceptada antes de pagar")
    completed = db.execute(
        text(
            """SELECT id FROM emergencias.pago
               WHERE cotizacion_id = :cid AND estado = 'COMPLETADO'
               LIMIT 1"""
        ),
        {"cid": body.cotizacion_id},
    ).first()
    if completed:
        raise HTTPException(409, "Esta cotizacion ya fue pagada")
    return cot


def _create_invoice(db, tenant_id: str, pago_id: str) -> str:
    numero = f"FAC-{datetime.utcnow().strftime('%Y%m%d')}-{pago_id[:8]}"
    db.execute(
        text(
            """INSERT INTO emergencias.factura (tenant_id, pago_id, numero, url_pdf)
               VALUES (:t, :p, :n, :url)
               ON CONFLICT (pago_id) DO NOTHING"""
        ),
        {
            "t": tenant_id,
            "p": pago_id,
            "n": numero,
            "url": f"/facturas/{numero}.pdf",
        },
    )
    return numero


def _complete_payment(db, pago_id: str, transaction_ref: str | None = None):
    pago = db.execute(
        text(
            """SELECT id, tenant_id, incidente_id, estado
               FROM emergencias.pago WHERE id = :id"""
        ),
        {"id": pago_id},
    ).mappings().first()
    if not pago:
        raise HTTPException(404, "Pago not found")
    if pago["estado"] != "COMPLETADO":
        db.execute(
            text(
                """UPDATE emergencias.pago
                   SET estado = 'COMPLETADO',
                       token_transaccion = COALESCE(:ref, token_transaccion),
                       pagado_at = COALESCE(pagado_at, now()),
                       updated_at = now()
                   WHERE id = :id"""
            ),
            {"id": pago_id, "ref": transaction_ref},
        )
        db.execute(
            text("UPDATE emergencias.incidente SET estado = 'PAGADO' WHERE id = :i"),
            {"i": str(pago["incidente_id"])},
        )
    factura = _create_invoice(db, str(pago["tenant_id"]), pago_id)
    return {"pago_id": pago_id, "estado": "COMPLETADO", "factura": factura}


@router.post("/intent")
def create_intent(
    body: PaymentIntentIn,
    tupla=Depends(require_permission("pago", "crear")),
):
    user, perm, db = tupla
    cot = _accepted_quote_for_driver(db, body, user.id)
    existing = db.execute(
        text(
            """SELECT id, token_transaccion FROM emergencias.pago
               WHERE cotizacion_id = :cid AND estado = 'PENDIENTE'
               ORDER BY created_at DESC LIMIT 1"""
        ),
        {"cid": body.cotizacion_id},
    ).mappings().first()
    if existing:
        return {"client_secret": existing["token_transaccion"], "pago_id": str(existing["id"])}

    pago_id = str(uuid.uuid4())
    if settings.stripe_secret_key:
        import stripe

        stripe.api_key = settings.stripe_secret_key
        intent = stripe.PaymentIntent.create(
            amount=int(float(cot["monto"]) * 100),
            currency="bob",
            metadata={
                "incidente_id": body.incidente_id,
                "cotizacion_id": body.cotizacion_id,
                "pago_id": pago_id,
                "tenant_id": str(cot["tenant_id"]),
            },
            idempotency_key=f"pay-{body.cotizacion_id}",
        )
        client_secret = intent.client_secret
        transaction_ref = intent.id
    else:
        client_secret = f"mock_secret_{pago_id}"
        transaction_ref = client_secret

    db.execute(
        text(
            """INSERT INTO emergencias.pago
            (id, tenant_id, incidente_id, cotizacion_id, monto, moneda, estado,
             metodo, pasarela, token_transaccion)
            VALUES (:id, :t, :i, :c, :m, 'BOB', 'PENDIENTE',
                    'tarjeta', 'stripe', :ref)"""
        ),
        {
            "id": pago_id,
            "t": str(cot["tenant_id"]),
            "i": body.incidente_id,
            "c": body.cotizacion_id,
            "m": cot["monto"],
            "ref": transaction_ref,
        },
    )
    return {"client_secret": client_secret, "pago_id": pago_id}


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
    db=Depends(get_admin_db),
):
    payload = await request.body()
    if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
        raise HTTPException(503, "Stripe webhook is not configured")
    import stripe

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
        )
    except Exception as exc:
        raise HTTPException(400, "Invalid Stripe signature") from exc

    if event["type"] != "payment_intent.succeeded":
        return {"received": True}
    intent = event["data"]["object"]
    metadata = intent.get("metadata", {}) or {}
    pago_id = metadata.get("pago_id")
    if not pago_id:
        raise HTTPException(400, "Missing pago_id metadata")
    return _complete_payment(db, pago_id, intent.get("id"))


@router.post("/mock-complete")
def mock_complete(
    body: PaymentIntentIn,
    tupla=Depends(require_permission("pago", "crear")),
):
    user, perm, db = tupla
    if settings.environment.lower() in {"prod", "production"}:
        raise HTTPException(403, "Mock payments are disabled in production")
    cot = _accepted_quote_for_driver(db, body, user.id)
    pago_id = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.pago
            (id, tenant_id, incidente_id, cotizacion_id, monto, moneda, estado,
             metodo, pasarela, token_transaccion, pagado_at)
            VALUES (:id, :t, :i, :c, :m, 'BOB', 'COMPLETADO',
                    'mock', 'mock', :ref, now())"""
        ),
        {
            "id": pago_id,
            "t": str(cot["tenant_id"]),
            "i": body.incidente_id,
            "c": body.cotizacion_id,
            "m": cot["monto"],
            "ref": f"mock_{pago_id}",
        },
    )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'PAGADO' WHERE id = :i"),
        {"i": body.incidente_id},
    )
    factura = _create_invoice(db, str(cot["tenant_id"]), pago_id)
    return {"pago_id": pago_id, "estado": "COMPLETADO", "factura": factura}


@router.get("/comisiones")
def comisiones(tupla=Depends(require_permission("pago", "leer"))):
    user, perm, db = tupla
    tid = user.tenant if not user.is_platform_admin else None
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_comisiones
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]
