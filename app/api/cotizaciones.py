import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import get_db, require_roles

router = APIRouter(tags=["cotizaciones"])


class CotizacionIn(BaseModel):
    monto: float
    detalle: str | None = None
    origen: str = "TALLER"


@router.post("/incidentes/{incidente_id}/cotizaciones", status_code=201)
def crear_cotizacion(
    incidente_id: str,
    body: CotizacionIn,
    user=Depends(require_roles("TALLER", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    cid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.cotizacion
            (id, tenant_id, incidente_id, monto, moneda, detalle, origen, estado)
            VALUES (:id, :t, :i, :m, 'BOB', :d, :o, 'PENDIENTE')"""
        ),
        {
            "id": cid,
            "t": user.tenant,
            "i": incidente_id,
            "m": body.monto,
            "d": body.detalle,
            "o": body.origen,
        },
    )
    return {"id": cid}


@router.post("/cotizaciones/{cotizacion_id}/aceptar")
def aceptar_cotizacion(
    cotizacion_id: str,
    user=Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    row = db.execute(
        text(
            """UPDATE emergencias.cotizacion SET estado = 'ACEPTADA'
            WHERE id = :id RETURNING id"""
        ),
        {"id": cotizacion_id},
    ).first()
    if not row:
        raise HTTPException(404, "Not found")
    return {"estado": "ACEPTADA"}
