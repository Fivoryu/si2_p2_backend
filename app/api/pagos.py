import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.config import settings
from ..core.deps import get_db, require_roles

router = APIRouter(prefix="/pagos", tags=["pagos"])


class PaymentIntentIn(BaseModel):
    incidente_id: str
    cotizacion_id: str


@router.post("/intent")
def create_intent(
    body: PaymentIntentIn,
    user=Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    cot = db.execute(
        text("SELECT monto, estado FROM emergencias.cotizacion WHERE id = :id"),
        {"id": body.cotizacion_id},
    ).first()
    if not cot:
        raise HTTPException(404, "Cotización not found")
    if cot[1] != "ACEPTADA":
        raise HTTPException(409, "La cotizacion debe estar aceptada antes de pagar")
    pago_id = str(uuid.uuid4())
    client_secret = None
    if settings.stripe_secret_key:
        import stripe

        stripe.api_key = settings.stripe_secret_key
        intent = stripe.PaymentIntent.create(
            amount=int(float(cot[0]) * 100),
            currency="bob",
            metadata={
                "incidente_id": body.incidente_id,
                "cotizacion_id": body.cotizacion_id,
                "pago_id": pago_id,
            },
        )
        client_secret = intent.client_secret
    else:
        client_secret = f"mock_secret_{pago_id}"
    db.execute(
        text(
            """INSERT INTO emergencias.pago
            (id, tenant_id, incidente_id, cotizacion_id, monto, moneda, estado, metodo, pasarela, token_transaccion)
            VALUES (:id, :t, :i, :c, :m, 'BOB', 'PENDIENTE', 'tarjeta', 'stripe', :ref)"""
        ),
        {
            "id": pago_id,
            "t": user.tenant,
            "i": body.incidente_id,
            "c": body.cotizacion_id,
            "m": cot[0],
            "ref": client_secret,
        },
    )
    return {"client_secret": client_secret, "pago_id": pago_id}


@router.post("/mock-complete")
def mock_complete(
    body: PaymentIntentIn,
    user=Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    """Demo payment without Stripe — registers pago + factura + PAGADO."""
    cot = db.execute(
        text("SELECT monto, estado FROM emergencias.cotizacion WHERE id = :id"),
        {"id": body.cotizacion_id},
    ).first()
    if not cot:
        raise HTTPException(404, "Cotización not found")
    if cot[1] != "ACEPTADA":
        raise HTTPException(409, "La cotizacion debe estar aceptada antes de pagar")
    pago_id = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.pago
            (id, tenant_id, incidente_id, cotizacion_id, monto, moneda, estado, metodo, pasarela, pagado_at)
            VALUES (:id, :t, :i, :c, :m, 'BOB', 'COMPLETADO', 'mock', 'mock', now())"""
        ),
        {
            "id": pago_id,
            "t": user.tenant,
            "i": body.incidente_id,
            "c": body.cotizacion_id,
            "m": cot[0],
        },
    )
    db.execute(
        text("UPDATE emergencias.incidente SET estado = 'PAGADO' WHERE id = :i"),
        {"i": body.incidente_id},
    )
    numero = f"FAC-{datetime.utcnow().strftime('%Y%m%d')}-{pago_id[:8]}"
    db.execute(
        text(
            """INSERT INTO emergencias.factura (tenant_id, pago_id, numero, url_pdf)
               VALUES (:t, :p, :n, :url)
               ON CONFLICT (pago_id) DO NOTHING"""
        ),
        {
            "t": user.tenant,
            "p": pago_id,
            "n": numero,
            "url": f"/facturas/{numero}.pdf",
        },
    )
    return {"pago_id": pago_id, "estado": "COMPLETADO", "factura": numero}


@router.get("/comisiones")
def comisiones(user=Depends(require_roles("TALLER", "ADMIN_TENANT")), db=Depends(get_db)):
    tid = user.tenant if not user.is_platform_admin else None
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_comisiones
            WHERE (:tid IS NULL OR tenant_id = :tid)"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]
