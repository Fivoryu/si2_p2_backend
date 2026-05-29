import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import get_db, require_roles

router = APIRouter(prefix="/tecnicos", tags=["tecnicos"])


class TecnicoIn(BaseModel):
    taller_id: str
    nombre: str
    telefono: str | None = None
    especialidad: str | None = None


@router.get("")
def list_tecnicos(db=Depends(get_db), limit: int = 50, offset: int = 0):
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.tecnico ORDER BY nombre
            LIMIT :limit OFFSET :offset"""
        ),
        {"limit": limit, "offset": offset},
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("", status_code=201)
def create_tecnico(
    body: TecnicoIn,
    user=Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    tid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.tecnico
            (id, tenant_id, taller_id, nombre, telefono, especialidad)
            VALUES (:id, :t, :tl, :n, :tel, :esp)"""
        ),
        {
            "id": tid,
            "t": user.tenant,
            "tl": body.taller_id,
            "n": body.nombre,
            "tel": body.telefono,
            "esp": body.especialidad,
        },
    )
    return {"id": tid}
