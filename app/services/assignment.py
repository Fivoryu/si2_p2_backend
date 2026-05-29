from datetime import datetime, timezone

from sqlalchemy import text

from ..core.db import scoped_session
from ..ws.manager import manager

CANDIDATE_SQL = text(
    """
WITH inc AS (
  SELECT latitud, longitud, tipo_incidente_id, tenant_id
  FROM emergencias.incidente WHERE id = :inc
)
SELECT t.id AS taller_id, t.nombre, t.calificacion, t.capacidad_max,
       (earth_distance(ll_to_earth(t.latitud, t.longitud),
                       ll_to_earth(inc.latitud, inc.longitud)) / 1000.0) AS distancia_km,
       (SELECT count(*) FROM emergencias.asignacion a
         WHERE a.taller_id = t.id AND a.estado IN ('ASIGNADO', 'ACEPTADO')) AS carga
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


def score(distancia_km: float, carga: int, calificacion: float, capacidad_max: int = 3):
    w_dist, w_load, w_rating = 0.5, 0.3, 0.2
    dist_score = 1.0 / (1.0 + float(distancia_km or 1))
    load_score = max(0.0, 1.0 - int(carga or 0) / max(capacidad_max or 1, 1))
    rating_score = float(calificacion or 3) / 5.0
    return round(w_dist * dist_score + w_load * load_score + w_rating * rating_score, 4)


async def assign_best_workshop(
    incidente_id: str, tenant_id: str, exclude_taller_ids: list[str] | None = None
):
    exclude = exclude_taller_ids or []
    db = scoped_session(tenant_id)
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
                text(
                    "UPDATE emergencias.incidente SET estado = 'NO_ATENDIDO' WHERE id = :i"
                ),
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

        ranked = sorted(
            cands,
            key=lambda c: score(
                c["distancia_km"],
                c["carga"],
                c["calificacion"],
                c.get("capacidad_max") or 3,
            ),
            reverse=True,
        )
        for c in ranked:
            db.execute(
                text(
                    """INSERT INTO emergencias.taller_candidato
                    (tenant_id, incidente_id, taller_id, distancia_km, tiempo_llegada_min, puntaje)
                    VALUES (:t, :i, :tl, :d, :eta, :s)
                    ON CONFLICT (incidente_id, taller_id) DO NOTHING"""
                ),
                {
                    "t": tenant_id,
                    "i": incidente_id,
                    "tl": c["taller_id"],
                    "d": round(float(c["distancia_km"] or 0), 2),
                    "eta": int(float(c["distancia_km"] or 1) * 2) + 3,
                    "s": score(
                        c["distancia_km"],
                        c["carga"],
                        c["calificacion"],
                        c.get("capacidad_max") or 3,
                    ),
                },
            )

        best = ranked[0]
        db.execute(
            text(
                """INSERT INTO emergencias.asignacion
                (tenant_id, incidente_id, taller_id, estado, asignacion_automatica)
                VALUES (:t, :i, :tl, 'ASIGNADO', true)"""
            ),
            {"t": tenant_id, "i": incidente_id, "tl": best["taller_id"]},
        )
        db.execute(
            text(
                "UPDATE emergencias.incidente SET estado = 'TALLER_ASIGNADO' WHERE id = :i"
            ),
            {"i": incidente_id},
        )
        db.commit()
        taller_nombre = best["nombre"]
        taller_id = str(best["taller_id"])
    finally:
        db.close()

    await manager.publish(
        tenant_id,
        incidente_id,
        {
            "type": "ASSIGNMENT",
            "incident_id": incidente_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": {
                "taller_id": taller_id,
                "taller_nombre": taller_nombre,
                "estado": "ASIGNADO",
            },
        },
    )
