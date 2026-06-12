from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user_verified, get_db, require_permission, require_roles

router = APIRouter(tags=["kpi"])


def _tenant_filter(user: CurrentUser, tenant_id: str | None) -> str | None:
    if user.is_platform_admin:
        return tenant_id
    return user.tenant


def _where(alias: str, tid: str | None, desde: str | None, hasta: str | None, tenant_alias: str | None = None) -> tuple[str, dict]:
    """Genera WHERE con tenant + rango de fechas sobre reportado_at.

    alias:        alias de la tabla incidente (para reportado_at).
    tenant_alias: alias de la tabla que tiene tenant_id (por defecto = alias).
    """
    ta = tenant_alias or alias
    parts = [f"(:tid IS NULL OR {ta}.tenant_id = CAST(:tid AS uuid))"]
    params: dict = {"tid": tid}
    if desde:
        parts.append(f"{alias}.reportado_at >= CAST(:desde AS date)")
        params["desde"] = desde
    if hasta:
        parts.append(f"{alias}.reportado_at < (CAST(:hasta AS date) + INTERVAL '1 day')")
        params["hasta"] = hasta
    return " AND ".join(parts), params


MV_WHERE = "(:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"


# =====================================================================
#  KPIs
# =====================================================================


@router.get("/kpis/resumen")
def kpis_resumen(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    COUNT(*)                                                          AS total_incidentes,
                    COUNT(*) FILTER (WHERE i.estado IN ('FINALIZADO','PAGADO'))       AS total_finalizados,
                    COUNT(*) FILTER (WHERE i.estado = 'CANCELADO')                    AS total_cancelados,
                    COUNT(*) FILTER (WHERE i.estado = 'NO_ATENDIDO')                  AS total_no_atendidos,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.asignado_at - i.reportado_at))/60.0)::numeric, 2)  AS prom_min_asignacion,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.atendido_at  - i.asignado_at))/60.0)::numeric, 2)  AS prom_min_llegada,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.aceptado_at  - i.asignado_at))/60.0)::numeric, 2)  AS prom_min_respuesta_taller,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0)::numeric, 2) AS prom_min_total,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE i.estado = 'CANCELADO') / NULLIF(COUNT(*),0), 2) AS pct_cancelacion
                FROM emergencias.incidente i
                WHERE {w}
                GROUP BY i.tenant_id"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_resumen_tenant WHERE {MV_WHERE}"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/por-tipo")
def kpis_por_tipo(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    COALESCE(ti.codigo, 'SIN_CLASIFICAR') AS tipo_codigo,
                    COALESCE(ti.nombre, 'Sin clasificar') AS tipo_nombre,
                    COUNT(*)                                AS total,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0)::numeric, 2) AS prom_min_total
                FROM emergencias.incidente i
                LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
                WHERE {w}
                GROUP BY i.tenant_id, ti.codigo, ti.nombre
                ORDER BY total DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_incidentes_por_tipo WHERE {MV_WHERE} ORDER BY total DESC"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/talleres")
def kpis_talleres(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT t.tenant_id, t.id AS taller_id, t.nombre AS taller_nombre, t.calificacion,
                    COUNT(a.id) FILTER (WHERE a.estado = 'ACEPTADO')  AS servicios_aceptados,
                    COUNT(a.id) FILTER (WHERE a.estado = 'RECHAZADO') AS servicios_rechazados,
                    ROUND(AVG(EXTRACT(EPOCH FROM (a.respondido_at - a.asignado_at))/60.0)
                        FILTER (WHERE a.estado = 'ACEPTADO')::numeric, 2) AS prom_min_respuesta,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0)
                        FILTER (WHERE i.estado IN ('FINALIZADO','PAGADO'))::numeric, 2) AS prom_min_finalizacion
                FROM emergencias.taller t
                JOIN emergencias.asignacion a ON a.taller_id = t.id
                JOIN emergencias.incidente i  ON i.id = a.incidente_id
                WHERE {w}
                GROUP BY t.tenant_id, t.id, t.nombre, t.calificacion
                ORDER BY servicios_aceptados DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_talleres_eficientes WHERE {MV_WHERE}"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/zonas")
def kpis_zonas(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    ROUND(i.latitud,  2) AS zona_lat,
                    ROUND(i.longitud, 2) AS zona_lng,
                    COUNT(*)             AS total_incidentes
                FROM emergencias.incidente i
                WHERE {w}
                  AND i.latitud IS NOT NULL AND i.longitud IS NOT NULL
                GROUP BY i.tenant_id, ROUND(i.latitud, 2), ROUND(i.longitud, 2)
                ORDER BY total_incidentes DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_zonas WHERE {MV_WHERE} ORDER BY total_incidentes DESC"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/sla")
def kpis_sla(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    ti.codigo   AS tipo_codigo,
                    ti.nombre   AS tipo_nombre,
                    s.tiempo_max_min,
                    COUNT(*)                                                    AS total_evaluados,
                    COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0 <= s.tiempo_max_min) AS dentro_sla,
                    COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0 >  s.tiempo_max_min) AS fuera_sla,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0 <= s.tiempo_max_min)
                        / NULLIF(COUNT(*), 0), 2) AS pct_cumplimiento
                FROM emergencias.incidente i
                JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
                JOIN emergencias.sla_config s ON s.tenant_id = i.tenant_id AND s.tipo_incidente_id = i.tipo_incidente_id
                WHERE {w}
                  AND i.finalizado_at IS NOT NULL
                GROUP BY i.tenant_id, ti.codigo, ti.nombre, s.tiempo_max_min
                ORDER BY pct_cumplimiento DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_sla WHERE {MV_WHERE} ORDER BY pct_cumplimiento DESC"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/comisiones")
def kpis_comisiones(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    t.nombre                                         AS taller_nombre,
                    COUNT(p.id)                                      AS total_pagos,
                    ROUND(SUM(p.monto)::numeric, 2)                  AS total_cobrado,
                    ROUND(SUM(p.comision_plataforma)::numeric, 2)    AS total_comision_plataforma,
                    ROUND(SUM(p.monto_taller)::numeric, 2)           AS total_neto_taller
                FROM emergencias.pago p
                JOIN emergencias.asignacion a ON a.incidente_id = p.incidente_id AND a.estado = 'ACEPTADO'
                JOIN emergencias.taller t     ON t.id = a.taller_id
                JOIN emergencias.incidente i  ON i.id = p.incidente_id
                WHERE p.estado = 'COMPLETADO'
                  AND {w}
                GROUP BY i.tenant_id, t.nombre
                ORDER BY total_comision_plataforma DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_comisiones WHERE {MV_WHERE} ORDER BY total_comision_plataforma DESC"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/taller-ranking")
def kpis_taller_ranking(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta, tenant_alias="t")
        rows = db.execute(
            text(
                f"""SELECT t.tenant_id, t.id AS taller_id, t.nombre AS taller_nombre,
                    t.calificacion AS rating_taller,
                    COUNT(a.id) FILTER (WHERE a.estado = 'ACEPTADO')  AS aceptadas,
                    COUNT(a.id) FILTER (WHERE a.estado = 'RECHAZADO') AS rechazadas,
                    ROUND(COALESCE(
                        COUNT(a.id) FILTER (WHERE a.estado = 'RECHAZADO')::numeric /
                        NULLIF(COUNT(a.id) FILTER (WHERE a.estado IN ('ACEPTADO','RECHAZADO')), 0), 0), 4) AS tasa_rechazo,
                    ROUND(COALESCE(
                        COUNT(a.id) FILTER (WHERE a.estado = 'ACEPTADO')::numeric /
                        NULLIF(COUNT(a.id) FILTER (WHERE a.estado IN ('ACEPTADO','RECHAZADO')), 0), 0), 4) AS tasa_aceptacion,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.atendido_at - i.en_camino_at))/60.0)
                        FILTER (WHERE i.atendido_at IS NOT NULL AND i.en_camino_at IS NOT NULL), 2) AS prom_llegada_min,
                    COUNT(i.id) FILTER (WHERE i.estado IN ('FINALIZADO','PAGADO')) AS servicios_finalizados,
                    ROUND(AVG(cs.estrellas), 2) AS rating_servicio
                FROM emergencias.taller t
                LEFT JOIN emergencias.asignacion a ON a.taller_id = t.id
                LEFT JOIN emergencias.incidente i  ON i.id = a.incidente_id
                LEFT JOIN emergencias.calificacion_servicio cs ON cs.taller_id = t.id
                WHERE {w}
                GROUP BY t.tenant_id, t.id, t.nombre, t.calificacion
                ORDER BY tasa_rechazo ASC, rating_taller DESC, prom_llegada_min ASC NULLS LAST"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""SELECT * FROM emergencias.mv_kpi_taller_ranking
                WHERE {MV_WHERE}
                ORDER BY tasa_rechazo ASC, rating_taller DESC, prom_llegada_min ASC NULLS LAST"""
            ),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/tecnico-ranking")
def kpis_tecnico_ranking(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta, tenant_alias="tec")
        rows = db.execute(
            text(
                f"""SELECT tec.tenant_id, tec.id AS tecnico_id, tec.taller_id,
                    tec.nombre AS tecnico_nombre,
                    COUNT(a.id) FILTER (WHERE a.estado = 'ACEPTADO') AS asignaciones_aceptadas,
                    COUNT(i.id) FILTER (WHERE i.estado IN ('FINALIZADO','PAGADO')) AS servicios_finalizados,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.atendido_at - i.en_camino_at))/60.0)
                        FILTER (WHERE i.atendido_at IS NOT NULL AND i.en_camino_at IS NOT NULL), 2) AS prom_llegada_min
                FROM emergencias.tecnico tec
                LEFT JOIN emergencias.asignacion a ON a.tecnico_id = tec.id
                LEFT JOIN emergencias.incidente i  ON i.id = a.incidente_id
                WHERE {w}
                GROUP BY tec.tenant_id, tec.id, tec.taller_id, tec.nombre
                ORDER BY servicios_finalizados DESC, prom_llegada_min ASC NULLS LAST"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""SELECT * FROM emergencias.mv_kpi_tecnico_ranking
                WHERE {MV_WHERE}
                ORDER BY servicios_finalizados DESC, prom_llegada_min ASC NULLS LAST"""
            ),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/demanda-hora")
def kpis_demanda_hora(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    date_trunc('hour', i.reportado_at)                          AS hora,
                    COUNT(*)                                                     AS total_incidentes,
                    ROUND(AVG(EXTRACT(EPOCH FROM (i.asignado_at - i.reportado_at))/60.0), 2) AS prom_asignacion_min
                FROM emergencias.incidente i
                WHERE {w}
                GROUP BY i.tenant_id, date_trunc('hour', i.reportado_at)
                ORDER BY hora DESC
                LIMIT 72"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""SELECT * FROM emergencias.mv_kpi_demanda_hora
                WHERE {MV_WHERE}
                ORDER BY hora DESC LIMIT 72"""
            ),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/precio-tipo")
def kpis_precio_tipo(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    ti.codigo                             AS tipo_codigo,
                    ti.nombre                             AS tipo_nombre,
                    ROUND(AVG(p.monto)::numeric, 2)       AS precio_promedio,
                    ROUND(MIN(p.monto)::numeric, 2)       AS precio_min,
                    ROUND(MAX(p.monto)::numeric, 2)       AS precio_max,
                    COUNT(p.id)                           AS total_pagos
                FROM emergencias.pago p
                JOIN emergencias.incidente i       ON i.id = p.incidente_id
                JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
                WHERE p.estado = 'COMPLETADO'
                  AND {w}
                GROUP BY i.tenant_id, ti.codigo, ti.nombre
                ORDER BY precio_promedio DESC"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_precio_promedio_tipo WHERE {MV_WHERE} ORDER BY precio_promedio DESC"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/precio-calidad")
def kpis_precio_calidad(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""WITH precio_taller AS (
                    SELECT t.tenant_id, t.id AS taller_id, t.nombre AS taller_nombre,
                        ROUND(AVG(p.monto)::numeric, 2) AS precio_promedio,
                        COUNT(p.id) AS servicios_pagados
                    FROM emergencias.taller t
                    JOIN emergencias.asignacion a ON a.taller_id = t.id AND a.estado = 'ACEPTADO'
                    JOIN emergencias.pago p       ON p.incidente_id = a.incidente_id AND p.estado = 'COMPLETADO'
                    JOIN emergencias.incidente i  ON i.id = p.incidente_id
                    WHERE {w}
                    GROUP BY t.tenant_id, t.id, t.nombre
                ),
                rating_taller AS (
                    SELECT taller_id,
                        ROUND(AVG(estrellas)::numeric, 2) AS rating_promedio,
                        COUNT(id) AS total_calificaciones
                    FROM emergencias.calificacion_servicio
                    GROUP BY taller_id
                )
                SELECT pt.tenant_id, pt.taller_id, pt.taller_nombre, pt.precio_promedio,
                    COALESCE(rt.rating_promedio, 0) AS rating_servicio,
                    pt.servicios_pagados, rt.total_calificaciones,
                    ROUND(CASE WHEN COALESCE(rt.rating_promedio, 0) > 0
                        THEN (pt.precio_promedio / rt.rating_promedio)::numeric
                        ELSE NULL END, 2) AS relacion_precio_calidad
                FROM precio_taller pt
                LEFT JOIN rating_taller rt ON rt.taller_id = pt.taller_id
                ORDER BY relacion_precio_calidad ASC NULLS LAST"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(f"SELECT * FROM emergencias.mv_kpi_precio_calidad WHERE {MV_WHERE} ORDER BY relacion_precio_calidad ASC NULLS LAST"),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/demanda-zona")
def kpis_demanda_zona(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tupla=Depends(require_permission("incidente", "leer")),
):
    user, perm, db = tupla
    tid = _tenant_filter(user, tenant_id)
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        rows = db.execute(
            text(
                f"""SELECT i.tenant_id,
                    ROUND(i.latitud,  2)                      AS zona_lat,
                    ROUND(i.longitud, 2)                      AS zona_lng,
                    date_trunc('day', i.reportado_at)::date   AS fecha,
                    COUNT(*)                                  AS total_incidentes
                FROM emergencias.incidente i
                WHERE {w}
                  AND i.latitud IS NOT NULL AND i.longitud IS NOT NULL
                GROUP BY i.tenant_id, ROUND(i.latitud, 2), ROUND(i.longitud, 2), date_trunc('day', i.reportado_at)
                ORDER BY fecha DESC, total_incidentes DESC
                LIMIT 500"""
            ),
            p,
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""SELECT * FROM emergencias.mv_kpi_demanda_zona
                WHERE {MV_WHERE}
                ORDER BY fecha DESC, total_incidentes DESC LIMIT 500"""
            ),
            {"tid": tid},
        ).mappings().all()
    return [dict(r) for r in rows]


# =====================================================================
#  Export CSV (con date filter)
# =====================================================================


@router.get("/kpis/export")
def kpis_export_csv(
    tenant_id: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    import io, csv as _csv

    tid = _tenant_filter(user, tenant_id)

    # Si hay fechas, usar queries contra tablas base; si no, vistas materializadas
    if desde or hasta:
        w, p = _where("i", tid, desde, hasta)
        p_no_date = {"tid": tid}
        sections = [
            ("RESUMEN", f"""SELECT i.tenant_id,
                COUNT(*) AS total_incidentes,
                COUNT(*) FILTER (WHERE i.estado IN ('FINALIZADO','PAGADO')) AS total_finalizados,
                COUNT(*) FILTER (WHERE i.estado = 'CANCELADO') AS total_cancelados,
                COUNT(*) FILTER (WHERE i.estado = 'NO_ATENDIDO') AS total_no_atendidos,
                ROUND(AVG(EXTRACT(EPOCH FROM (i.asignado_at - i.reportado_at))/60.0)::numeric, 2) AS prom_min_asignacion,
                ROUND(AVG(EXTRACT(EPOCH FROM (i.atendido_at  - i.asignado_at))/60.0)::numeric, 2) AS prom_min_llegada,
                ROUND(AVG(EXTRACT(EPOCH FROM (i.finalizado_at - i.reportado_at))/60.0)::numeric, 2) AS prom_min_total,
                ROUND(100.0 * COUNT(*) FILTER (WHERE i.estado = 'CANCELADO') / NULLIF(COUNT(*),0), 2) AS pct_cancelacion
                FROM emergencias.incidente i WHERE {w} GROUP BY i.tenant_id""", p),
            ("INCIDENTES_POR_TIPO", f"""SELECT i.tenant_id, COALESCE(ti.nombre,'Sin clasificar') AS tipo_nombre,
                COUNT(*) AS total FROM emergencias.incidente i
                LEFT JOIN emergencias.tipo_incidente ti ON ti.id = i.tipo_incidente_id
                WHERE {w} GROUP BY i.tenant_id, ti.nombre ORDER BY total DESC""", p),
            ("COMISIONES", f"""SELECT t.nombre AS taller_nombre,
                COUNT(p.id) AS total_pagos, ROUND(SUM(p.monto)::numeric, 2) AS total_cobrado,
                ROUND(SUM(p.comision_plataforma)::numeric, 2) AS total_comision_plataforma,
                ROUND(SUM(p.monto_taller)::numeric, 2) AS total_neto_taller
                FROM emergencias.pago p
                JOIN emergencias.asignacion a ON a.incidente_id = p.incidente_id AND a.estado = 'ACEPTADO'
                JOIN emergencias.taller t ON t.id = a.taller_id
                JOIN emergencias.incidente i ON i.id = p.incidente_id
                WHERE p.estado = 'COMPLETADO' AND {w} GROUP BY t.nombre ORDER BY total_comision_plataforma DESC""", p),
        ]
    else:
        p = {"tid": tid}
        sections = [
            ("RESUMEN", f"SELECT * FROM emergencias.mv_kpi_resumen_tenant WHERE {MV_WHERE}", p),
            ("INCIDENTES_POR_TIPO", f"SELECT * FROM emergencias.mv_kpi_incidentes_por_tipo WHERE {MV_WHERE} ORDER BY total DESC", p),
            ("TALLERES_EFICIENTES", f"SELECT * FROM emergencias.mv_kpi_talleres_eficientes WHERE {MV_WHERE} ORDER BY servicios_aceptados DESC", p),
            ("ZONAS", f"SELECT * FROM emergencias.mv_kpi_zonas WHERE {MV_WHERE} ORDER BY total_incidentes DESC", p),
            ("SLA", f"SELECT * FROM emergencias.mv_kpi_sla WHERE {MV_WHERE} ORDER BY pct_cumplimiento DESC", p),
            ("COMISIONES", f"SELECT * FROM emergencias.mv_kpi_comisiones WHERE {MV_WHERE} ORDER BY total_comision_plataforma DESC", p),
            ("TALLER_RANKING", f"SELECT * FROM emergencias.mv_kpi_taller_ranking WHERE {MV_WHERE} ORDER BY tasa_rechazo ASC, rating_taller DESC", p),
            ("TECNICO_RANKING", f"SELECT * FROM emergencias.mv_kpi_tecnico_ranking WHERE {MV_WHERE} ORDER BY servicios_finalizados DESC", p),
            ("DEMANDA_HORA", f"SELECT * FROM emergencias.mv_kpi_demanda_hora WHERE {MV_WHERE} ORDER BY hora DESC LIMIT 72", p),
            ("PRECIO_TIPO", f"SELECT * FROM emergencias.mv_kpi_precio_promedio_tipo WHERE {MV_WHERE} ORDER BY precio_promedio DESC", p),
            ("PRECIO_CALIDAD", f"SELECT * FROM emergencias.mv_kpi_precio_calidad WHERE {MV_WHERE} ORDER BY relacion_precio_calidad ASC NULLS LAST", p),
            ("DEMANDA_ZONA", f"SELECT * FROM emergencias.mv_kpi_demanda_zona WHERE {MV_WHERE} ORDER BY fecha DESC, total_incidentes DESC LIMIT 500", p),
        ]

    output = io.StringIO()
    writer = _csv.writer(output)

    for label, sql, params in sections:
        rows = db.execute(text(sql), params).mappings().all()
        if not rows:
            writer.writerow([f"--- {label} ---"])
            writer.writerow(["(sin datos)"])
            writer.writerow([])
            continue
        writer.writerow([f"--- {label} ---"])
        writer.writerow(list(rows[0].keys()))
        for r in rows:
            writer.writerow([str(v) if v is not None else "" for v in r.values()])
        writer.writerow([])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="kpis-export.csv"'},
    )


# =====================================================================
#  Refresh
# =====================================================================


@router.post("/kpis/refresh")
def refresh_kpis(
    tupla=Depends(require_permission("incidente", "actualizar")),
):
    user, perm, db = tupla
    db.execute(text("SELECT emergencias.refrescar_kpis()"))
    return {"ok": True}


# =====================================================================
#  SLA CRUD
# =====================================================================


class SlaIn(BaseModel):
    tipo_incidente_id: str
    tiempo_max_min: int


@router.get("/sla")
def list_sla(
    tupla=Depends(require_permission("sla_config", "leer")),
):
    user, perm, db = tupla
    rows = db.execute(text("SELECT * FROM emergencias.sla_config")).mappings().all()
    return [dict(r) for r in rows]


@router.post("/sla", status_code=201)
def create_sla(
    body: SlaIn,
    tupla=Depends(require_permission("sla_config", "crear")),
):
    user, perm, db = tupla
    import uuid

    sid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.sla_config
            (id, tenant_id, tipo_incidente_id, tiempo_max_min)
            VALUES (:id, :t, :tp, :tm)"""
        ),
        {
            "id": sid,
            "t": user.tenant,
            "tp": body.tipo_incidente_id,
            "tm": body.tiempo_max_min,
        },
    )
    return {"id": sid}


class SlaPatch(BaseModel):
    tiempo_max_min: int


@router.patch("/sla/{sla_id}")
def patch_sla(
    sla_id: str,
    body: SlaPatch,
    tupla=Depends(require_permission("sla_config", "actualizar")),
):
    user, perm, db = tupla
    db.execute(
        text(
            """UPDATE emergencias.sla_config
            SET tiempo_max_min = :tm, updated_at = now()
            WHERE id = :id"""
        ),
        {"tm": body.tiempo_max_min, "id": sla_id},
    )
    return {"ok": True}
