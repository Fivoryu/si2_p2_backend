import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.db import scoped_session
from ..core.deps import CurrentUser, require_permission
from ..core.state_machine import CANCELABLE, can_transition
from ..schemas.incidente import (
    CancelarIn,
    EstadoPatch,
    IncidenteCreate,
    IncidenteOut,
    SimularIn,
    UbicacionIn,
)
from ..services.ai import run_ai_pipeline
from ..services.access import can_access_incident, can_manage_incident_service
from ..services.estado_ws import emit_estado_cambio, marcar_llegada_tecnico
from ..services.notifications import notify_estado_change
from ..services.routing import (
    es_geocerca_cercana,
    generar_puntos_interpolados,
    obtener_ruta,
)
from ..services.transcription import transcribe_audio_bytes
from ..ws.manager import manager

router = APIRouter(prefix="/incidentes", tags=["incidentes"])

_simulation_cancel: dict[str, asyncio.Event] = {}


class CalificacionIn(BaseModel):
    estrellas: int = Field(ge=1, le=5)
    comentario: str | None = None


def _row_to_out(row) -> IncidenteOut:
    return IncidenteOut(
        id=row["id"],
        estado=row["estado"],
        prioridad=row["prioridad"],
        tipo_incidente_id=row.get("tipo_incidente_id"),
        latitud=float(row["latitud"]) if row.get("latitud") is not None else None,
        longitud=float(row["longitud"]) if row.get("longitud") is not None else None,
        resumen_ia=row.get("resumen_ia"),
        reportado_at=row["reportado_at"],
        vehiculo_id=row.get("vehiculo_id"),
        descripcion=row.get("descripcion"),
    )


@router.post("", status_code=201, response_model=IncidenteOut)
def crear_incidente(
    body: IncidenteCreate,
    bg: BackgroundTasks,
    tupla=Depends(require_permission("incidente", "crear")),
):
    user, perm, db = tupla
    vehicle = db.execute(
        text(
            """SELECT 1 FROM emergencias.vehiculo
               WHERE id = :v AND conductor_id = :c"""
        ),
        {"v": str(body.vehiculo_id), "c": user.id},
    ).first()
    if not vehicle:
        raise HTTPException(403, "Vehiculo no pertenece al conductor")
    inc_id = str(uuid.uuid4())
    ext = str(body.external_id) if body.external_id else None
    # Conductor sin tenant → tenant_id NULL (incidente visible a todos los talleres)
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, estado, prioridad,
             descripcion, latitud, longitud, direccion, external_id, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, 'PENDIENTE', 'INCIERTA',
                    :d, :la, :lo, :dir, :ext, 'SINCRONIZADO')"""
        ),
        {
            "id": inc_id,
            "t": user.tenant,  # puede ser NULL para conductores sin taller
            "c": user.id,
            "v": str(body.vehiculo_id),
            "d": body.descripcion,
            "la": body.latitud,
            "lo": body.longitud,
            "dir": body.direccion,
            "ext": ext,
        },
    )
    db.commit()
    row = db.execute(
        text("SELECT * FROM emergencias.incidente WHERE id = :id"),
        {"id": inc_id},
    ).mappings().first()
    bg.add_task(run_ai_pipeline, inc_id, user.tenant, 5.0)
    out = _row_to_out(row)
    return out


@router.get("")
def list_incidentes(
    tupla=Depends(require_permission("incidente", "leer")),
    estado: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    user, perm, db = tupla
    where = "WHERE 1=1"
    params = {"limit": limit, "offset": offset}
    if user.rol == "CONDUCTOR":
        where += " AND conductor_id = :uid"
        params["uid"] = user.id
    elif user.rol == "TALLER":
        where += """ AND id IN (
            SELECT incidente_id FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE t.usuario_id = :uid)"""
        params["uid"] = user.id
    if estado:
        where += " AND estado = :estado"
        params["estado"] = estado
    total = db.execute(
        text(f"SELECT count(*) FROM emergencias.incidente {where}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id, estado, prioridad, tipo_incidente_id, latitud, longitud,
                resumen_ia, reportado_at, vehiculo_id, descripcion
            FROM emergencias.incidente {where}
            ORDER BY reportado_at DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    items = [_row_to_out(r) for r in rows]
    items_dicts = [perm.filter_dict("incidente", i.model_dump()) for i in items]
    return {"items": items_dicts, "total": total}


@router.get("/{incidente_id}")
def get_incidente(
    incidente_id: str,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    inc = db.execute(
        text("SELECT * FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    if not can_access_incident(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    evs = db.execute(
        text("SELECT * FROM emergencias.evidencia WHERE incidente_id = :id"),
        {"id": incidente_id},
    ).mappings().all()
    asig = db.execute(
        text(
            """SELECT a.*, t.nombre AS taller_nombre FROM emergencias.asignacion a
            JOIN emergencias.taller t ON t.id = a.taller_id
            WHERE a.incidente_id = :id ORDER BY a.created_at DESC LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().first()
    ofertas = db.execute(
        text(
            """SELECT c.id, c.taller_id, t.nombre AS taller_nombre, c.monto,
                      c.precio_sugerido, c.tiempo_estimado_min, c.estado,
                      c.comentario_taller, t.calificacion
               FROM emergencias.cotizacion c
               JOIN emergencias.taller t ON t.id = c.taller_id
               WHERE c.incidente_id = :id
               ORDER BY c.estado = 'ACEPTADA' DESC, c.monto ASC"""
        ),
        {"id": incidente_id},
    ).mappings().all()
    ultima_ubicacion = db.execute(
        text(
            """SELECT latitud, longitud, tecnico_id, es_fake, created_at
               FROM emergencias.ubicacion_tracking
               WHERE incidente_id = :id
               ORDER BY created_at DESC
               LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().first()
    return {
        "incidente": perm.filter_dict("incidente", dict(inc)),
        "evidencias": perm.filter_list("evidencia", [dict(e) for e in evs]),
        "asignacion": perm.filter_dict("asignacion", dict(asig)) if asig else None,
        "ofertas": perm.filter_list("cotizacion", [dict(o) for o in ofertas]),
        "ultima_ubicacion": dict(ultima_ubicacion) if ultima_ubicacion else None,
    }


@router.patch("/{incidente_id}/estado")
async def patch_estado(
    incidente_id: str,
    body: EstadoPatch,
    tupla=Depends(require_permission("incidente", "actualizar")),
):
    user, perm, db = tupla
    inc = db.execute(
        text("SELECT estado, tenant_id FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    if not can_access_incident(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    if not can_manage_incident_service(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    old = inc["estado"]
    if old == body.estado:
        return {"estado": body.estado, "unchanged": True}
    if not can_transition(old, body.estado):
        raise HTTPException(409, f"Invalid transition {old} -> {body.estado}")
    db.execute(
        text("UPDATE emergencias.incidente SET estado = :e WHERE id = :id"),
        {"e": body.estado, "id": incidente_id},
    )
    db.commit()
    tenant = str(inc["tenant_id"])
    lat = lng = None
    if body.estado == "EN_ATENCION":
        ultima = db.execute(
            text(
                """SELECT latitud, longitud FROM emergencias.ubicacion_tracking
                   WHERE incidente_id = :id ORDER BY created_at DESC LIMIT 1"""
            ),
            {"id": incidente_id},
        ).mappings().first()
        if ultima:
            lat = float(ultima["latitud"])
            lng = float(ultima["longitud"])
    await emit_estado_cambio(
        db,
        tenant,
        incidente_id,
        old,
        body.estado,
        comentario=body.comentario,
        lat=lat,
        lng=lng,
    )
    return {"estado": body.estado}


@router.post("/{incidente_id}/cancelar")
def cancelar(
    incidente_id: str,
    body: CancelarIn,
    tupla=Depends(require_permission("incidente", "actualizar")),
):
    user, perm, db = tupla
    inc = db.execute(
        text(
            "SELECT estado FROM emergencias.incidente WHERE id = :id AND conductor_id = :c"
        ),
        {"id": incidente_id, "c": user.id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    if inc["estado"] not in CANCELABLE:
        raise HTTPException(409, "Cannot cancel in current state")
    db.execute(
        text(
            """UPDATE emergencias.incidente
            SET estado = 'CANCELADO', motivo_cancelacion = :m WHERE id = :id"""
        ),
        {"m": body.motivo, "id": incidente_id},
    )
    db.commit()
    return {"estado": "CANCELADO"}


@router.post("/{incidente_id}/evidencias", status_code=201)
async def add_evidencia(
    incidente_id: str,
    bg: BackgroundTasks,
    tipo: str = Form(...),
    texto: str | None = Form(None),
    file: UploadFile | None = File(None),
    tupla=Depends(require_permission("evidencia", "crear")),
):
    user, perm, db = tupla
    url = None
    owner = db.execute(
        text(
            """SELECT 1 FROM emergencias.incidente
               WHERE id = :id AND conductor_id = :c"""
        ),
        {"id": incidente_id, "c": user.id},
    ).first()
    if not owner:
        raise HTTPException(404, "Incidente no encontrado o sin permiso")
    if file:
        data = await file.read()
        key = f"{user.tenant}/{incidente_id}/{uuid.uuid4()}"
        try:
            from ..core.aws import upload_bytes

            upload_bytes(key, data, file.content_type or "application/octet-stream")
            url = key
        except Exception:
            from ..core.aws import save_local_evidencia

            url = save_local_evidencia(key, data)
    eid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.evidencia
            (id, tenant_id, incidente_id, tipo, url, contenido_texto)
            VALUES (:id, :t, :i, :tipo, :url, :txt)"""
        ),
        {
            "id": eid,
            "t": user.tenant,
            "i": incidente_id,
            "tipo": tipo,
            "url": url,
            "txt": texto,
        },
    )
    db.commit()
    if tipo == "IMAGEN":
        bg.add_task(run_ai_pipeline, incidente_id, user.tenant, 0.0)
    elif tipo == "AUDIO" and file:
        bg.add_task(_transcribir_y_trigger, eid, incidente_id, user.tenant, data, file.content_type or "audio/aac")
    return {"id": eid, "url": url}


async def _transcribir_y_trigger(evidencia_id: str, incidente_id: str, tenant_id: str, data: bytes, mime: str):
    texto = None
    try:
        texto, _ = transcribe_audio_bytes(data, mime)
    except Exception:
        pass
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
    from ..services.ai import run_ai_pipeline
    await run_ai_pipeline(incidente_id, tenant_id)


@router.get("/{incidente_id}/historial")
def historial(
    incidente_id: str,
    tupla=Depends(require_permission("incidente_estado_historial", "leer")),
):
    user, perm, db = tupla
    if not can_access_incident(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.incidente_estado_historial
            WHERE incidente_id = :id ORDER BY created_at"""
        ),
        {"id": incidente_id},
    ).mappings().all()
    items = perm.filter_list("incidente_estado_historial", [dict(r) for r in rows])
    return {"items": items}


@router.post("/{incidente_id}/calificacion", status_code=201)
def calificar_servicio(
    incidente_id: str,
    body: CalificacionIn,
    tupla=Depends(require_permission("calificacion_servicio", "crear")),
):
    user, perm, db = tupla
    inc = db.execute(
        text(
            """SELECT i.id, i.tenant_id, i.conductor_id, i.estado, a.taller_id
               FROM emergencias.incidente i
               JOIN emergencias.asignacion a ON a.incidente_id = i.id AND a.estado = 'ACEPTADO'
               WHERE i.id = :i"""
        ),
        {"i": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Incidente no encontrado o sin taller aceptado")
    if str(inc["conductor_id"]) != user.id:
        raise HTTPException(403, "No puedes calificar este incidente")
    if inc["estado"] not in {"FINALIZADO", "PAGADO"}:
        raise HTTPException(409, "Solo se puede calificar un servicio finalizado o pagado")

    cal_id = str(uuid.uuid4())
    try:
        db.execute(
            text(
                """INSERT INTO emergencias.calificacion_servicio
                (id, tenant_id, incidente_id, taller_id, conductor_id, estrellas, comentario)
                VALUES (:id, :t, :i, :tl, :c, :e, :comentario)"""
            ),
            {
                "id": cal_id,
                "t": str(inc["tenant_id"]),
                "i": incidente_id,
                "tl": str(inc["taller_id"]),
                "c": user.id,
                "e": body.estrellas,
                "comentario": body.comentario,
            },
        )
    except Exception as exc:
        if "uq_calificacion_incidente" in str(exc):
            raise HTTPException(409, "Este incidente ya fue calificado") from exc
        raise

    db.execute(
        text(
            """UPDATE emergencias.taller
               SET calificacion = (
                 SELECT ROUND(AVG(estrellas)::numeric, 2)
                 FROM emergencias.calificacion_servicio
                 WHERE taller_id = :tl
               )
               WHERE id = :tl"""
        ),
        {"tl": str(inc["taller_id"])},
    )
    db.commit()
    return {"id": cal_id, "estrellas": body.estrellas}


@router.post("/{incidente_id}/ubicacion")
async def post_ubicacion(
    incidente_id: str,
    body: UbicacionIn,
    tupla=Depends(require_permission("ubicacion_tracking", "crear")),
):
    user, perm, db = tupla
    inc = db.execute(
        text("SELECT tenant_id FROM emergencias.incidente WHERE id = :id"),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Not found")
    if not can_manage_incident_service(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    params = {
        "t": str(inc["tenant_id"]),
        "i": incidente_id,
        "la": body.lat,
        "lo": body.lng,
        "tec": body.tecnico_id if body.tecnico_id else None,
        "fake": body.es_fake,
        "vel": body.velocidad_kmh,
        "prec": body.precision_m,
    }
    try:
        db.execute(
            text(
                """INSERT INTO emergencias.ubicacion_tracking
                (tenant_id, incidente_id, latitud, longitud, tecnico_id, es_fake,
                 velocidad_kmh, precision_m)
                VALUES (:t, :i, :la, :lo, :tec, :fake, :vel, :prec)"""
            ),
            params,
        )
    except Exception:
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(inc["tenant_id"])},
        )
        db.execute(
            text(
                """INSERT INTO emergencias.ubicacion_tracking
                (tenant_id, incidente_id, latitud, longitud, tecnico_id, es_fake)
                VALUES (:t, :i, :la, :lo, :tec, :fake)"""
            ),
            params,
        )
    db.commit()
    tenant = str(inc["tenant_id"])
    await manager.publish(
        tenant,
        incidente_id,
        {
            "type": "TECH_LOCATION",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "lat": body.lat,
                "lng": body.lng,
                "tecnico_id": body.tecnico_id,
                "velocidad_kmh": body.velocidad_kmh,
                "precision_m": body.precision_m,
            },
        },
    )
    return {"ok": True}


@router.get("/{incidente_id}/ruta")
async def get_ruta_incidente(
    incidente_id: str,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    if not can_access_incident(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")

    inc = db.execute(
        text(
            """SELECT i.latitud, i.longitud, t.latitud AS t_lat, t.longitud AS t_lng
               FROM emergencias.incidente i
               LEFT JOIN emergencias.asignacion a
                 ON a.incidente_id = i.id AND a.estado = 'ACEPTADO'
               LEFT JOIN emergencias.taller t ON t.id = a.taller_id
               WHERE i.id = :id"""
        ),
        {"id": incidente_id},
    ).mappings().first()
    if not inc or inc["latitud"] is None or inc["longitud"] is None:
        raise HTTPException(404, "Incidente sin coordenadas")

    destino = (float(inc["longitud"]), float(inc["latitud"]))
    primera = db.execute(
        text(
            """SELECT latitud, longitud FROM emergencias.ubicacion_tracking
               WHERE incidente_id = :id ORDER BY created_at ASC LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().first()

    if primera:
        origen = (float(primera["longitud"]), float(primera["latitud"]))
    elif inc["t_lng"] is not None and inc["t_lat"] is not None:
        origen = (float(inc["t_lng"]), float(inc["t_lat"]))
    else:
        raise HTTPException(409, "Sin origen para calcular ruta")

    ruta, motor = await obtener_ruta(origen, destino, usar_osrm=True)
    polyline = [{"lat": lat, "lng": lon} for lon, lat in ruta.coords]
    return {
        "coords": polyline,
        "distancia_km": round(ruta.distancia_km, 3),
        "duracion_est_seg": round(ruta.duracion_seg, 1),
        "motor_ruta": motor,
    }


@router.post("/{incidente_id}/simular")
async def simular_ruta(
    incidente_id: str,
    background_tasks: BackgroundTasks,
    body: SimularIn = None,
    tupla=Depends(require_permission("incidente", "actualizar")),
):
    user, perm, db = tupla
    if body is None:
        body = SimularIn()

    inc = db.execute(
        text(
            """SELECT i.tenant_id, i.latitud, i.longitud, i.estado,
                      t.latitud AS t_lat, t.longitud AS t_lng,
                      a.tecnico_id
               FROM emergencias.incidente i
               JOIN emergencias.asignacion a ON a.incidente_id = i.id AND a.estado = 'ACEPTADO'
               JOIN emergencias.taller t ON t.id = a.taller_id
               WHERE i.id = :id"""
        ),
        {"id": incidente_id},
    ).mappings().first()
    if not inc:
        raise HTTPException(404, "Incidente no encontrado o sin asignación aceptada")
    if not can_manage_incident_service(db, incidente_id, user):
        raise HTTPException(403, "Forbidden for this incident")
    if inc["estado"] not in {"TALLER_ASIGNADO", "EN_CAMINO"}:
        raise HTTPException(409, "El incidente debe estar asignado o en camino")

    if inc["latitud"] is None or inc["longitud"] is None:
        raise HTTPException(409, "El incidente no tiene coordenadas de destino")

    destino = (float(inc["longitud"]), float(inc["latitud"]))

    ultima = db.execute(
        text(
            """SELECT latitud, longitud FROM emergencias.ubicacion_tracking
               WHERE incidente_id = :id ORDER BY created_at DESC LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().first()

    if body.origen_lng is not None and body.origen_lat is not None:
        origen = (float(body.origen_lng), float(body.origen_lat))
    elif ultima:
        origen = (float(ultima["longitud"]), float(ultima["latitud"]))
    elif inc["t_lng"] is not None and inc["t_lat"] is not None:
        origen = (float(inc["t_lng"]), float(inc["t_lat"]))
    else:
        raise HTTPException(409, "No hay origen para la ruta")

    ruta, motor = await obtener_ruta(origen, destino, usar_osrm=body.usar_osrm)

    velocidad = body.velocidad_kmh
    intervalo = body.intervalo_seg
    duracion_sim_seg: float | None = None
    if body.duracion_sim_min is not None and body.duracion_sim_min > 0:
        horas = body.duracion_sim_min / 60.0
        if ruta.distancia_km > 0:
            velocidad = max(15.0, ruta.distancia_km / horas)
        duracion_sim_seg = body.duracion_sim_min * 60.0
        intervalo = min(body.intervalo_seg, max(0.25, duracion_sim_seg / 120.0))

    puntos = await generar_puntos_interpolados(
        ruta.coords,
        velocidad,
        intervalo,
        ruta.distancia_km,
    )
    if not puntos:
        puntos = [(origen[0], origen[1], 0.0), (destino[0], destino[1], body.intervalo_seg)]

    tenant = str(inc["tenant_id"])
    tecnico_id = str(inc["tecnico_id"]) if inc.get("tecnico_id") else None

    polyline = [{"lat": lat, "lng": lon} for lon, lat in ruta.coords]
    duracion_ws = duracion_sim_seg if duracion_sim_seg is not None else ruta.duracion_seg
    await manager.publish(
        tenant,
        incidente_id,
        {
            "type": "ROUTE_POLYLINE",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "coords": polyline,
                "distancia_km": round(ruta.distancia_km, 3),
                "duracion_est_seg": round(duracion_ws, 1),
                "duracion_sim_seg": round(duracion_ws, 1),
                "velocidad_sim_kmh": round(velocidad, 1),
                "motor_ruta": motor,
                "simulacion_activa": True,
            },
        },
    )

    prev = _simulation_cancel.pop(incidente_id, None)
    if prev is not None:
        prev.set()
    cancel_event = asyncio.Event()
    _simulation_cancel[incidente_id] = cancel_event

    async def _enviar_punto(lon: float, lat: float, fake: bool):
        db2 = scoped_session(tenant)
        try:
            db2.execute(
                text(
                    """INSERT INTO emergencias.ubicacion_tracking
                    (tenant_id, incidente_id, latitud, longitud, tecnico_id, es_fake)
                    VALUES (:t, :i, :la, :lo, :tec, :fake)"""
                ),
                {
                    "t": tenant,
                    "i": incidente_id,
                    "la": lat,
                    "lo": lon,
                    "tec": tecnico_id,
                    "fake": fake,
                },
            )
            db2.commit()
        finally:
            db2.close()

        await manager.broadcast_tech_location(
            tenant,
            incidente_id,
            lat=lat,
            lng=lon,
            tecnico_id=tecnico_id,
        )

    async def _finalizar_llegada(lon: float, lat: float) -> None:
        db3 = scoped_session(tenant)
        try:
            await marcar_llegada_tecnico(db3, tenant, incidente_id, lat, lon)
        finally:
            db3.close()

    async def _simular():
        intervalo_sleep = max(0.2, intervalo)
        llego = False
        try:
            for i, (lon, lat, _) in enumerate(puntos):
                if cancel_event.is_set():
                    break
                await _enviar_punto(lon, lat, body.usar_fake)

                if es_geocerca_cercana((lon, lat), destino, radio_m=80):
                    await _finalizar_llegada(lon, lat)
                    llego = True
                    break

                if i < len(puntos) - 1:
                    await asyncio.sleep(intervalo_sleep)

            if not llego and puntos and not cancel_event.is_set():
                lon, lat, _ = puntos[-1]
                await _finalizar_llegada(lon, lat)
        finally:
            _simulation_cancel.pop(incidente_id, None)
            await manager.publish(
                tenant,
                incidente_id,
                {
                    "type": "SIMULATION_ENDED",
                    "incident_id": incidente_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": {},
                },
            )

    asyncio.create_task(_simular())
    return {
        "ok": True,
        "mensaje": "Simulación iniciada",
        "puntos": len(puntos),
        "distancia_km": round(ruta.distancia_km, 3),
        "duracion_sim_seg": round(duracion_ws, 1),
        "velocidad_sim_kmh": round(velocidad, 1),
        "polyline": polyline,
        "motor_ruta": motor,
    }
