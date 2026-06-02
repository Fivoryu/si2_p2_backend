import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles

router = APIRouter(prefix="/tecnicos", tags=["tecnicos"])


class TecnicoIn(BaseModel):
    taller_id: str
    nombre: str
    telefono: str | None = None
    especialidad: str | None = None
    especialidad_ids: list[str] | None = None


class TecnicoUpdate(BaseModel):
    nombre: str | None = None
    telefono: str | None = None
    especialidad_ids: list[str] | None = None
    disponible: bool | None = None


def _resolve_especialidad_labels(db, esp_ids: list[str], taller_id: str) -> list[str]:
    if not esp_ids:
        return []
    rows = db.execute(
        text(
            """SELECT id, nombre FROM emergencias.especialidad_taller
            WHERE taller_id = :tl AND activo = true"""
        ),
        {"tl": taller_id},
    ).mappings().all()
    found = {str(r["id"]): r["nombre"] for r in rows}
    missing = [eid for eid in esp_ids if eid not in found]
    if missing:
        raise HTTPException(400, "Una o más especialidades no pertenecen al taller")
    return [found[eid] for eid in esp_ids]


def _sync_tecnico_especialidades(db, tecnico_id: str, taller_id: str, esp_ids: list[str]) -> str | None:
    labels = _resolve_especialidad_labels(db, esp_ids, taller_id)
    db.execute(
        text("DELETE FROM emergencias.tecnico_especialidad WHERE tecnico_id = :tid"),
        {"tid": tecnico_id},
    )
    for eid in esp_ids:
        db.execute(
            text(
                """INSERT INTO emergencias.tecnico_especialidad (tecnico_id, especialidad_id)
                VALUES (:t, :e)"""
            ),
            {"t": tecnico_id, "e": eid},
        )
    return ", ".join(labels) if labels else None


def _enrich_tecnicos(db, rows) -> list[dict]:
    items = []
    for row in rows:
        item = dict(row)
        esp_rows = db.execute(
            text(
                """SELECT e.id, e.nombre
                FROM emergencias.tecnico_especialidad te
                JOIN emergencias.especialidad_taller e ON e.id = te.especialidad_id
                WHERE te.tecnico_id = :tid
                ORDER BY e.nombre"""
            ),
            {"tid": item["id"]},
        ).mappings().all()
        item["especialidad_ids"] = [str(r["id"]) for r in esp_rows]
        item["especialidades"] = [r["nombre"] for r in esp_rows]
        if esp_rows and not item.get("especialidad"):
            item["especialidad"] = ", ".join(item["especialidades"])
        items.append(item)
    return items


@router.get("")
def list_tecnicos(db=Depends(get_db), limit: int = 50, offset: int = 0):
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.tecnico ORDER BY nombre
            LIMIT :limit OFFSET :offset"""
        ),
        {"limit": limit, "offset": offset},
    ).mappings().all()
    items = _enrich_tecnicos(db, rows)
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
def create_tecnico(
    body: TecnicoIn,
    user=Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    if user.rol == "TALLER":
        own = db.execute(
            text(
                """SELECT id FROM emergencias.taller
                WHERE id = :tl AND usuario_id = :uid"""
            ),
            {"tl": body.taller_id, "uid": user.id},
        ).first()
        if not own:
            raise HTTPException(403, "Solo puede registrar técnicos en su taller")

    tid = str(uuid.uuid4())
    esp_summary = body.especialidad
    if body.especialidad_ids:
        labels = _resolve_especialidad_labels(db, body.especialidad_ids, body.taller_id)
        esp_summary = ", ".join(labels)

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
            "esp": esp_summary,
        },
    )
    if body.especialidad_ids:
        _sync_tecnico_especialidades(db, tid, body.taller_id, body.especialidad_ids)
    return {"id": tid}


@router.patch("/{tecnico_id}")
def update_tecnico(
    tecnico_id: str,
    body: TecnicoUpdate,
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "TALLER")),
    db=Depends(get_db),
):
    row = db.execute(
        text("SELECT id, taller_id FROM emergencias.tecnico WHERE id = :id"),
        {"id": tecnico_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Técnico no encontrado")

    if user.rol == "TALLER":
        own = db.execute(
            text(
                """SELECT 1 FROM emergencias.taller
                WHERE id = :tl AND usuario_id = :uid"""
            ),
            {"tl": row["taller_id"], "uid": user.id},
        ).first()
        if not own:
            raise HTTPException(403, "Sin permiso para editar este técnico")

    sets: list[str] = []
    params: dict = {"id": tecnico_id}
    if body.nombre is not None:
        sets.append("nombre = :n")
        params["n"] = body.nombre
    if body.telefono is not None:
        sets.append("telefono = :tel")
        params["tel"] = body.telefono
    if body.disponible is not None:
        sets.append("disponible = :d")
        params["d"] = body.disponible
    if body.especialidad_ids is not None:
        esp_summary = _sync_tecnico_especialidades(
            db, tecnico_id, str(row["taller_id"]), body.especialidad_ids
        )
        sets.append("especialidad = :esp")
        params["esp"] = esp_summary

    if sets:
        sql = f"UPDATE emergencias.tecnico SET {', '.join(sets)} WHERE id = :id"
        db.execute(text(sql), params)

    return {"ok": True}
