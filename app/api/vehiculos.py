import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles

router = APIRouter(prefix="/vehiculos", tags=["vehiculos"])


class VehiculoIn(BaseModel):
    placa: str
    marca: str
    modelo: str
    anio: int | None = None
    color: str | None = None
    tipo_combustible: str | None = None


@router.get("")
def list_vehiculos(
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.vehiculo
            WHERE conductor_id = :c ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset"""
        ),
        {"c": user.id, "limit": limit, "offset": offset},
    ).mappings().all()
    total = db.execute(
        text("SELECT count(*) FROM emergencias.vehiculo WHERE conductor_id = :c"),
        {"c": user.id},
    ).scalar()
    return {"items": [dict(r) for r in rows], "total": total}


@router.post("", status_code=201)
def create_vehiculo(
    body: VehiculoIn,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    dup = db.execute(
        text(
            """SELECT id FROM emergencias.vehiculo
            WHERE conductor_id = :c AND placa = :p"""
        ),
        {"c": user.id, "p": body.placa.upper()},
    ).first()
    if dup:
        raise HTTPException(409, "Placa already registered")
    vid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.vehiculo
            (id, tenant_id, conductor_id, placa, marca, modelo, anio, color, tipo_combustible)
            VALUES (:id, :t, :c, :p, :m, :mo, :a, :co, :tc)"""
        ),
        {
            "id": vid,
            "t": user.tenant,
            "c": user.id,
            "p": body.placa.upper(),
            "m": body.marca,
            "mo": body.modelo,
            "a": body.anio,
            "co": body.color,
            "tc": body.tipo_combustible,
        },
    )
    return {"id": vid}


@router.patch("/{vehiculo_id}")
def patch_vehiculo(
    vehiculo_id: str,
    body: VehiculoIn,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    db.execute(
        text(
            """UPDATE emergencias.vehiculo
            SET placa = :p, marca = :m, modelo = :mo, anio = :a, color = :co, tipo_combustible = :tc
            WHERE id = :id AND conductor_id = :c"""
        ),
        {
            "id": vehiculo_id,
            "c": user.id,
            "p": body.placa.upper(),
            "m": body.marca,
            "mo": body.modelo,
            "a": body.anio,
            "co": body.color,
            "tc": body.tipo_combustible,
        },
    )
    return {"id": vehiculo_id}


@router.delete("/{vehiculo_id}", status_code=204)
def delete_vehiculo(
    vehiculo_id: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    db.execute(
        text("DELETE FROM emergencias.vehiculo WHERE id = :id AND conductor_id = :c"),
        {"id": vehiculo_id, "c": user.id},
    )
