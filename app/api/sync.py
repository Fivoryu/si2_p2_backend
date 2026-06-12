import base64
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text

from ..core.deps import require_permission
from ..schemas.sync import SyncBatch
from ..services.ai import run_ai_pipeline
from ..services.transcription import transcribe_audio_bytes

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("")
def sync(
    body: SyncBatch,
    bg: BackgroundTasks,
    tupla=Depends(require_permission("incidente", "crear")),
):
    user, perm, db = tupla
    if len(body.incidentes) > 20:
        raise HTTPException(413, "El lote offline no puede superar 20 incidentes")
    out = []
    for item in body.incidentes:
        owns_vehicle = db.execute(
            text(
                """SELECT 1 FROM emergencias.vehiculo
                WHERE id = :v AND conductor_id = :c"""
            ),
            {"v": str(item.vehiculo_id), "c": user.id},
        ).first()
        if not owns_vehicle:
            raise HTTPException(403, "Vehiculo no pertenece al conductor")
        if len(item.evidencias) > 6:
            raise HTTPException(413, "Cada incidente offline acepta maximo 6 evidencias")
        client_updated_at = item.client_updated_at
        if client_updated_at > datetime.now(timezone.utc) + timedelta(minutes=10):
            client_updated_at = datetime.now(timezone.utc)
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
                    "cu": client_updated_at,
                },
            )
            db.execute(
                text(
                    """UPDATE emergencias.sync_mapping SET last_write_at = :cu
                    WHERE tenant_id = :t AND external_id = :e"""
                ),
                {"cu": client_updated_at, "t": user.tenant, "e": str(item.external_id)},
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
                "cu": client_updated_at,
            },
        )
        for ev in item.evidencias:
            eid = str(uuid.uuid4())
            url = None
            texto = ev.texto
            if ev.contenido_b64:
                try:
                    if len(ev.contenido_b64) > 8 * 1024 * 1024:
                        raise HTTPException(413, "Evidencia offline demasiado grande")
                    data = base64.b64decode(ev.contenido_b64)
                    mime = ev.mime_type or "application/octet-stream"
                    key = f"{user.tenant}/{inc_id}/{eid}"
                    try:
                        from ..core.aws import upload_bytes

                        upload_bytes(key, data, mime)
                        url = key
                    except Exception:
                        url = f"local://{key}"
                    if ev.tipo == "AUDIO" and not ev.texto:
                        texto = None
                except HTTPException:
                    raise
                except Exception:
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
                    "txt": texto,
                },
            )
            if ev.tipo == "AUDIO" and ev.contenido_b64:
                bg.add_task(_transcribir_audio_sync, str(eid), ev.contenido_b64, ev.mime_type or "audio/aac")
        bg.add_task(run_ai_pipeline, inc_id, user.tenant)
        out.append(
            {
                "external_id": str(item.external_id),
                "incidente_id": inc_id,
                "status": "CREATED",
            }
        )
    return {"results": out}


def _transcribir_audio_sync(evidencia_id: str, contenido_b64: str, mime: str):
    try:
        data = base64.b64decode(contenido_b64)
        texto, _ = transcribe_audio_bytes(data, mime)
        from ..core.db import SessionLocal
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE emergencias.evidencia SET transcripcion = :t WHERE id = :id"),
                {"t": texto, "id": evidencia_id},
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
