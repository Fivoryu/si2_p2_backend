from datetime import datetime, timezone

from sqlalchemy import text

from ..core.db import scoped_session
from .access import best_tecnico_for_assignment
from .pricing import calculate_service_offer
from .notifications import notify_workshop_new_assignment
from ..ws.manager import manager

CANDIDATE_SQL = text(
    """
WITH inc AS (
  SELECT i.latitud, i.longitud, i.tipo_incidente_id, i.tenant_id, i.prioridad,
         i.conductor_id,
         ti.codigo AS tipo_codigo
  FROM emergencias.incidente i
  LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
  WHERE i.id = :inc
)
SELECT t.id AS taller_id, t.nombre, t.calificacion, t.capacidad_max,
       inc.tipo_codigo, inc.prioridad,
       (earth_distance(ll_to_earth(t.latitud, t.longitud),
                       ll_to_earth(inc.latitud, inc.longitud)) / 1000.0) AS distancia_km,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id AND a.estado IN ('ASIGNADO', 'ACEPTADO')) AS carga,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id
           AND a.estado = 'RECHAZADO'
           AND a.respondido_at >= now() - interval '7 days') AS rechazos_7d,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id
           AND a.estado = 'RECHAZADO'
           AND a.respondido_at >= now() - interval '30 days') AS rechazos_30d,
       (SELECT count(*) FROM emergencias.asignacion a
         JOIN emergencias.incidente ix ON ix.id = a.incidente_id
         WHERE a.taller_id = t.id
           AND a.estado = 'RECHAZADO'
           AND ix.conductor_id = inc.conductor_id) AS rechazos_mismo_cliente,
       (SELECT count(*) FROM emergencias.asignacion a
         JOIN emergencias.incidente ix ON ix.id = a.incidente_id
         WHERE a.taller_id = t.id
           AND a.estado = 'RECHAZADO'
           AND ix.tipo_incidente_id = inc.tipo_incidente_id) AS rechazos_mismo_tipo,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id AND a.estado = 'ACEPTADO') AS aceptadas_total,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id AND a.estado IN ('ACEPTADO', 'RECHAZADO')) AS respuestas_total,
       (SELECT count(*) FROM emergencias.incidente ix
         JOIN emergencias.asignacion ax ON ax.incidente_id = ix.id
         WHERE ax.taller_id = t.id
           AND ix.estado IN ('FINALIZADO', 'PAGADO')) AS finalizados_total,
       (SELECT avg(EXTRACT(EPOCH FROM (ix.atendido_at - ix.en_camino_at)) / 60.0)
         FROM emergencias.incidente ix
         JOIN emergencias.asignacion ax ON ax.incidente_id = ix.id
         WHERE ax.taller_id = t.id
           AND ix.atendido_at IS NOT NULL
           AND ix.en_camino_at IS NOT NULL) AS prom_llegada_min,
       (SELECT count(*) FROM emergencias.incidente ix
         WHERE ix.tenant_id = inc.tenant_id
           AND ix.estado IN ('PENDIENTE', 'BUSCANDO_TALLER', 'TALLER_ASIGNADO', 'EN_CAMINO')
           AND ix.reportado_at >= now() - interval '2 hours') AS demanda_activa,
       (SELECT count(*) FROM emergencias.taller tx
         JOIN emergencias.taller_servicio tsx ON tsx.taller_id = tx.id
         WHERE tx.tenant_id = inc.tenant_id
           AND tx.disponible = true
           AND tx.activo = true
           AND tsx.tipo_incidente_id = inc.tipo_incidente_id) AS talleres_disponibles
FROM emergencias.taller t
JOIN inc ON t.tenant_id = inc.tenant_id
JOIN emergencias.taller_servicio ts
  ON ts.taller_id = t.id AND ts.tipo_incidente_id = inc.tipo_incidente_id
WHERE t.disponible = true AND t.activo = true
  AND inc.latitud IS NOT NULL AND inc.longitud IS NOT NULL
  AND (earth_distance(ll_to_earth(t.latitud, t.longitud),
                      ll_to_earth(inc.latitud, inc.longitud)) / 1000.0) <= 50
ORDER BY distancia_km ASC
LIMIT 10
"""
)


def rejection_penalty(c: dict) -> float:
    respuestas = max(int(c.get("respuestas_total") or 0), 1)
    tasa_rechazo = int(c.get("rechazos_30d") or 0) / respuestas
    raw = (
        int(c.get("rechazos_7d") or 0) * 0.08
        + int(c.get("rechazos_30d") or 0) * 0.025
        + int(c.get("rechazos_mismo_cliente") or 0) * 0.12
        + int(c.get("rechazos_mismo_tipo") or 0) * 0.08
        + tasa_rechazo * 0.25
    )
    return min(max(raw, 0.0), 1.0)


def score(c: dict):
    distancia_km = c.get("distancia_km")
    carga = c.get("carga")
    calificacion = c.get("calificacion")
    capacidad_max = c.get("capacidad_max") or 3
    dist_score = 1.0 / (1.0 + float(distancia_km or 1))
    load_score = max(0.0, 1.0 - int(carga or 0) / max(capacidad_max or 1, 1))
    rating_score = float(calificacion or 3) / 5.0
    accepted = int(c.get("aceptadas_total") or 0)
    total = max(int(c.get("respuestas_total") or 0), 1)
    eficiencia_score = min(max(accepted / total, 0.0), 1.0)
    prom_llegada = float(c.get("prom_llegada_min") or 35.0)
    sla_score = max(0.0, min(1.0, 1.0 - (prom_llegada / 90.0)))
    especialidad_score = 1.0
    disponibilidad_score = 1.0
    penalty = rejection_penalty(c)
    result = (
        dist_score * 0.25
        + disponibilidad_score * 0.15
        + rating_score * 0.15
        + eficiencia_score * 0.15
        + especialidad_score * 0.10
        + sla_score * 0.10
        + load_score * 0.05
        - penalty * 0.15
    )
    return round(max(result, 0.0), 4)


async def broadcast_to_workshops(
    incidente_id: str, tenant_id: str, exclude_taller_ids: list[str] | None = None
):
    """Broadcast emergency to all qualified workshops.

    Creates one asignacion (estado=ASIGNADO) per candidate workshop.
    Incident stays BUSCANDO_TALLER until conductor selects an offer.
    """
    exclude = exclude_taller_ids or []
    db = scoped_session(tenant_id)
    notified: list[dict] = []
    try:
        inc = db.execute(
            text("SELECT estado, tipo_incidente_id FROM emergencias.incidente WHERE id = :i"),
            {"i": incidente_id},
        ).mappings().first()
        if not inc or not inc["tipo_incidente_id"]:
            return

        cands = db.execute(CANDIDATE_SQL, {"inc": incidente_id}).mappings().all()
        cands = [c for c in cands if str(c["taller_id"]) not in exclude]

        if not cands:
            db.execute(
                text("UPDATE emergencias.incidente SET estado = 'NO_ATENDIDO' WHERE id = :i"),
                {"i": incidente_id},
            )
            db.commit()
            await manager.publish(
                tenant_id,
                incidente_id,
                {
                    "type": "STATUS_CHANGED",
                    "incident_id": incidente_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": {"estado_nuevo": "NO_ATENDIDO"},
                },
            )
            return

        ranked = sorted(cands, key=lambda c: score(dict(c)), reverse=True)

        for c in ranked:
            cdict = dict(c)
            penalty = rejection_penalty(cdict)
            pricing = calculate_service_offer(
                c.get("tipo_codigo"),
                c.get("prioridad"),
                c["distancia_km"],
                c["calificacion"],
                c["carga"],
                demanda_activa=c.get("demanda_activa"),
                talleres_disponibles=c.get("talleres_disponibles"),
                eficiencia=(
                    float(c.get("aceptadas_total") or 0)
                    / max(float(c.get("respuestas_total") or 1), 1.0)
                ),
                rechazo_penalty=penalty,
            )
            taller_tenant_cand = db.execute(
                text("SELECT tenant_id FROM emergencias.taller WHERE id = :id"),
                {"id": c["taller_id"]},
            ).scalar()

            # Save candidate score
            db.execute(
                text(
                    """INSERT INTO emergencias.taller_candidato
                    (tenant_id, incidente_id, taller_id, distancia_km, tiempo_llegada_min, puntaje,
                     precio_sugerido, dificultad)
                    VALUES (:t, :i, :tl, :d, :eta, :s, :precio, :dif)
                    ON CONFLICT (incidente_id, taller_id) DO NOTHING"""
                ),
                {
                    "t": taller_tenant_cand,
                    "i": incidente_id,
                    "tl": c["taller_id"],
                    "d": round(float(c["distancia_km"] or 0), 2),
                    "eta": pricing.tiempo_llegada_min,
                    "s": score(cdict),
                    "precio": pricing.precio_sugerido,
                    "dif": pricing.dificultad,
                },
            )

            # Create asignacion so taller can create an offer via aceptar-con-oferta
            tecnico_id = best_tecnico_for_assignment(
                db,
                str(c["taller_id"]),
                str(inc["tipo_incidente_id"]) if inc.get("tipo_incidente_id") else None,
            )
            db.execute(
                text(
                    """INSERT INTO emergencias.asignacion
                    (tenant_id, incidente_id, taller_id, tecnico_id, estado, asignacion_automatica)
                    VALUES (:t, :i, :tl, :tec, 'ASIGNADO', true)"""
                ),
                {"t": taller_tenant_cand, "i": incidente_id, "tl": c["taller_id"], "tec": tecnico_id},
            )
            notified.append({
                "taller_id": str(c["taller_id"]),
                "taller_nombre": c.get("nombre", ""),
                "tenant_id": taller_tenant_cand,
                "score": score(cdict),
            })

        # Incident stays BUSCANDO_TALLER until conductor selects an offer
        db.commit()
    finally:
        db.close()

    for t in notified:
        try:
            await notify_workshop_new_assignment(
                tenant_id, t["taller_id"], incidente_id, t["taller_nombre"]
            )
        except Exception:
            pass

    await manager.publish(
        tenant_id,
        incidente_id,
        {
            "type": "OFFERS_AVAILABLE",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {"candidatos": len(notified)},
        },
    )


# Keep old name as alias for backward compatibility with test files
assign_best_workshop = broadcast_to_workshops
