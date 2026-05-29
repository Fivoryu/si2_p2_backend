import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(
    db: Session,
    *,
    tenant_id: str | None,
    usuario_id: str | None,
    accion: str,
    entidad: str | None = None,
    entidad_id: str | None = None,
    detalle: dict | None = None,
):
    db.execute(
        text(
            """INSERT INTO emergencias.auditoria
            (tenant_id, usuario_id, accion, entidad, entidad_id, detalle)
            VALUES (:t, :u, :a, :e, :eid, CAST(:d AS jsonb))"""
        ),
        {
            "t": tenant_id,
            "u": usuario_id,
            "a": accion,
            "e": entidad,
            "eid": entidad_id,
            "d": json.dumps(detalle or {}),
        },
    )


def paginate(db: Session, sql: str, params: dict, limit: int, offset: int):
    count_sql = f"SELECT count(*) FROM ({sql}) q"
    total = db.execute(text(count_sql), params).scalar() or 0
    rows = db.execute(
        text(f"{sql} LIMIT :limit OFFSET :offset"),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], int(total)


def str_uuid(v) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, UUID) else str(v)
