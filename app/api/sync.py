import base64
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import text

from ..core.deps import get_db, require_roles
from ..schemas.sync import SyncBatch
from ..services.ai import run_ai_pipeline

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("")
def sync(
    body: SyncBatch,
    bg: BackgroundTasks,
    user=Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    out = []
    for item in body.incidentes:
        existing = db.execute(
            text(
                """SELECT incidente_id FROM emergencias.sync_mapping
                WHERE tenant_id = :t AND external_id = :e"""
            ),
            {"t": user.tenant, "e": str(item.external_id)},
        ).first()

        if existing:
            inc_id = str(existing[0])
            db.execute(
                text(
                    """UPDATE emergencias.incidente
                    SET descripcion = :d, latitud = :la, longitud = :lo, direccion = :dir,
                        updated_at = :cu
                    WHERE id = :i AND updated_at < :cu"""
                ),
                {
                    "d": item.descripcion,
                    "la": item.latitud,
                    "lo": item.longitud,
                    "dir": item.direccion,
                    "i": inc_id,
                    "cu": item.client_updated_at,
                },
            )
            db.execute(
                text(
                    """UPDATE emergencias.sync_mapping SET last_write_at = :cu
                    WHERE tenant_id = :t AND external_id = :e"""
                ),
                {"cu": item.client_updated_at, "t": user.tenant, "e": str(item.external_id)},
            )
            out.append(
                {
                    "external_id": str(item.external_id),
                    "incidente_id": inc_id,
                    "status": "UPDATED",
                }
            )
            continue

        inc_id = str(uuid.uuid4())
        db.execute(
            text(
                """INSERT INTO emergencias.incidente
                (id, tenant_id, conductor_id, vehiculo_id, estado, external_id,
                 estado_sincronizacion, dispositivo_origen, descripcion, latitud, longitud,
                 direccion, reportado_at)
                VALUES (:id, :t, :c, :v, 'PENDIENTE', :e, 'SINCRONIZADO', :dev,
                        :d, :la, :lo, :dir, :rep)"""
            ),
            {
                "id": inc_id,
                "t": user.tenant,
                "c": user.id,
                "v": str(item.vehiculo_id),
                "e": str(item.external_id),
                "dev": body.dispositivo,
                "d": item.descripcion,
                "la": item.latitud,
                "lo": item.longitud,
                "dir": item.direccion,
                "rep": item.client_created_at,
            },
        )
        db.execute(
            text(
                """INSERT INTO emergencias.sync_mapping
                (tenant_id, external_id, incidente_id, dispositivo, last_write_at)
                VALUES (:t, :e, :i, :dev, :cu)"""
            ),
            {
                "t": user.tenant,
                "e": str(item.external_id),
                "i": inc_id,
                "dev": body.dispositivo,
                "cu": item.client_updated_at,
            },
        )
        for ev in item.evidencias:
            eid = str(uuid.uuid4())
            url = None
            if ev.contenido_b64:
                url = f"sync/{inc_id}/{eid}"
            db.execute(
                text(
                    """INSERT INTO emergencias.evidencia
                    (id, tenant_id, incidente_id, tipo, url, contenido_texto)
                    VALUES (:id, :t, :i, :tipo, :url, :txt)"""
                ),
                {
                    "id": eid,
                    "t": user.tenant,
                    "i": inc_id,
                    "tipo": ev.tipo,
                    "url": url,
                    "txt": ev.texto,
                },
            )
        bg.add_task(run_ai_pipeline, inc_id, user.tenant)
        out.append(
            {
                "external_id": str(item.external_id),
                "incidente_id": inc_id,
                "status": "CREATED",
            }
        )
    return {"results": out}
