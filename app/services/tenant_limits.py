from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text


def assert_tenant_can_create_workshop(db, tenant_id: str) -> None:
    row = db.execute(
        text(
            """SELECT p.max_talleres,
                      (SELECT count(*) FROM emergencias.taller WHERE tenant_id = :t) AS actuales
               FROM emergencias.tenant tn
               JOIN emergencias.plan p ON p.id = tn.plan_id
               WHERE tn.id = :t"""
        ),
        {"t": tenant_id},
    ).mappings().first()
    if row and row["max_talleres"] is not None and int(row["actuales"]) >= int(row["max_talleres"]):
        raise HTTPException(409, "El plan del tenant no permite mas talleres")


def assert_tenant_can_create_technician(db, tenant_id: str) -> None:
    row = db.execute(
        text(
            """SELECT p.max_tecnicos,
                      (SELECT count(*) FROM emergencias.tecnico WHERE tenant_id = :t) AS actuales
               FROM emergencias.tenant tn
               JOIN emergencias.plan p ON p.id = tn.plan_id
               WHERE tn.id = :t"""
        ),
        {"t": tenant_id},
    ).mappings().first()
    if row and row["max_tecnicos"] is not None and int(row["actuales"]) >= int(row["max_tecnicos"]):
        raise HTTPException(409, "El plan del tenant no permite mas tecnicos")
