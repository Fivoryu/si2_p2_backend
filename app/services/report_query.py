"""
Ejecutor de queries estructurados para reportes dinámicos.

Toma un query JSON (producido por nl_reports) y lo convierte en SQL,
ejecuta contra la BD, y devuelve datos + recomendación de visualización
+ resumen textual.

Uso:
    from app.services.report_query import execute_report_query
    result = execute_report_query(query_dict, tenant_id="...", db=session)
"""
from datetime import datetime, timedelta

from sqlalchemy import text


# ============================================================================
# Mapeo de métricas a SQL
# ============================================================================

METRIC_SQL = {
    "count": "COUNT(*)",
    "avg_llegada": "ROUND(AVG(EXTRACT(EPOCH FROM (i.atendido_at - i.en_camino_at))/60.0)::numeric, 2)",
    "avg_asignacion": "ROUND(AVG(EXTRACT(EPOCH FROM (i.asignado_at - i.reportado_at))/60.0)::numeric, 2)",
    "avg_calificacion": "ROUND(AVG(COALESCE(cs.estrellas, t.calificacion, 0))::numeric, 2)",
    "tasa_rechazo": "ROUND(COALESCE(COUNT(a.id) FILTER (WHERE a.estado = 'RECHAZADO')::numeric / NULLIF(COUNT(a.id) FILTER (WHERE a.estado IN ('ACEPTADO','RECHAZADO')), 0), 0), 4)",
    "total_ingresos": "ROUND(SUM(p.monto)::numeric, 2)",
    "total_comisiones": "ROUND(SUM(p.comision_plataforma)::numeric, 2)",
    "sum_monto": "ROUND(SUM(p.monto)::numeric, 2)",
}

METRIC_LABEL = {
    "count": "total",
    "avg_llegada": "tiempo prom. llegada (min)",
    "avg_asignacion": "tiempo prom. asignación (min)",
    "avg_calificacion": "calificación promedio",
    "tasa_rechazo": "tasa de rechazo",
    "total_ingresos": "ingresos totales (Bs)",
    "total_comisiones": "comisiones totales (Bs)",
    "sum_monto": "monto total (Bs)",
}

ENTITY_TABLE = {
    "incidente": "emergencias.incidente i",
    "pago": "emergencias.pago p JOIN emergencias.incidente i ON i.id = p.incidente_id",
    "taller": "emergencias.taller t LEFT JOIN emergencias.asignacion a ON a.taller_id = t.id LEFT JOIN emergencias.incidente i ON i.id = a.incidente_id",
    "tecnico": "emergencias.tecnico tec LEFT JOIN emergencias.asignacion a ON a.tecnico_id = tec.id LEFT JOIN emergencias.incidente i ON i.id = a.incidente_id",
    "asignacion": "emergencias.asignacion a JOIN emergencias.incidente i ON i.id = a.incidente_id",
}

GROUP_BY_SQL = {
    "taller_id": ("t.id", "t.nombre AS grupo_nombre"),
    "tipo_codigo": ("ti.codigo", "ti.nombre AS grupo_nombre"),
    "zona": ("ROUND(i.latitud, 2) || ',' || ROUND(i.longitud, 2)", "ROUND(i.latitud, 2) AS zona_lat, ROUND(i.longitud, 2) AS zona_lng"),
    "dia": ("date_trunc('day', i.reportado_at)::date", "date_trunc('day', i.reportado_at)::date AS grupo_fecha"),
    "semana": ("date_trunc('week', i.reportado_at)::date", "date_trunc('week', i.reportado_at)::date AS grupo_fecha"),
    "mes": ("date_trunc('month', i.reportado_at)::date", "date_trunc('month', i.reportado_at)::date AS grupo_fecha"),
    "hora": ("date_trunc('hour', i.reportado_at)", "date_trunc('hour', i.reportado_at) AS grupo_hora"),
    "estado": ("i.estado", "i.estado AS grupo_nombre"),
}

# Columna raw para GROUP BY (sin alias)
GROUP_BY_RAW = {
    k: v[0] for k, v in GROUP_BY_SQL.items()
}

TIME_FILTER_SQL = {
    "hoy": "i.reportado_at >= CURRENT_DATE",
    "ayer": "i.reportado_at >= CURRENT_DATE - INTERVAL '1 day' AND i.reportado_at < CURRENT_DATE",
    "ultima_semana": "i.reportado_at >= CURRENT_DATE - INTERVAL '7 days'",
    "ultimo_mes": "i.reportado_at >= CURRENT_DATE - INTERVAL '30 days'",
    "ultimo_trimestre": "i.reportado_at >= CURRENT_DATE - INTERVAL '90 days'",
    "ultimo_anio": "i.reportado_at >= CURRENT_DATE - INTERVAL '365 days'",
}

# ============================================================================
# Construcción de SQL
# ============================================================================

def build_sql(query: dict, tenant_id: str) -> tuple[str, dict]:
    """
    Convierte un query dict a SQL parametrizado.

    Returns: (sql_string, params_dict)
    """
    intent = query.get("intent", "COUNT")
    entity = query.get("entity", "incidente")
    metric = query.get("metric", "count")
    group_by = query.get("group_by", "none")
    filters = query.get("filters", {})
    time_range = query.get("time_range", "none")
    limit = query.get("limit", 50)
    order = query.get("order", "desc")

    # FROM clause
    from_clause = ENTITY_TABLE.get(entity, "emergencias.incidente i")

    # SELECT clause
    metric_sql = METRIC_SQL.get(metric, "COUNT(*)")
    metric_label = METRIC_LABEL.get(metric, "valor")

    if group_by != "none" and group_by in GROUP_BY_SQL:
        gb_col, gb_label = GROUP_BY_SQL[group_by]
        gb_raw = GROUP_BY_RAW[group_by]
        select_clause = f"{gb_col} AS grupo, {gb_label}, {metric_sql} AS valor"
        group_clause = f"GROUP BY {gb_raw}"
    else:
        select_clause = f"{metric_sql} AS valor"
        group_clause = ""

    # WHERE clause
    where_parts = [f"i.tenant_id = CAST(:tid AS uuid)"]
    params = {"tid": tenant_id}

    if time_range != "none" and time_range in TIME_FILTER_SQL:
        where_parts.append(f"({TIME_FILTER_SQL[time_range]})")

    # Filtros
    if "tipo_codigo" in filters:
        where_parts.append("i.tipo_incidente_id IN (SELECT id FROM emergencias.tipo_incidente WHERE codigo = :tipo)")
        params["tipo"] = filters["tipo_codigo"]

    if "estado" in filters:
        where_parts.append("i.estado = :estado")
        params["estado"] = filters["estado"]

    if "prioridad" in filters:
        where_parts.append("i.prioridad = :prioridad")
        params["prioridad"] = filters["prioridad"]

    if "zona" in filters:
        zona = filters["zona"]
        zona_bounds = {
            "norte": {"lat_min": -17.35, "lat_max": -17.30, "lng_min": -66.15, "lng_max": -66.10},
            "sur": {"lat_min": -17.45, "lat_max": -17.40, "lng_min": -66.15, "lng_max": -66.10},
            "centro": {"lat_min": -17.42, "lat_max": -17.38, "lng_min": -66.16, "lng_max": -66.12},
            "este": {"lat_min": -17.42, "lat_max": -17.38, "lng_min": -66.12, "lng_max": -66.08},
            "oeste": {"lat_min": -17.42, "lat_max": -17.38, "lng_min": -66.20, "lng_max": -66.16},
        }
        if zona in zona_bounds:
            b = zona_bounds[zona]
            where_parts.append(
                "i.latitud BETWEEN :z_lat_min AND :z_lat_max AND i.longitud BETWEEN :z_lng_min AND :z_lng_max"
            )
            params.update({"z_lat_min": b["lat_min"], "z_lat_max": b["lat_max"],
                           "z_lng_min": b["lng_min"], "z_lng_max": b["lng_max"]})

    where_clause = "WHERE " + " AND ".join(where_parts)

    # ORDER BY + LIMIT
    order_clause = ""
    if group_by != "none":
        order_dir = "DESC" if order == "desc" else "ASC"
        order_clause = f"ORDER BY valor {order_dir}"

    if intent == "RANKING" or (intent in ("COUNT", "AGGREGATE") and group_by != "none"):
        limit_clause = f"LIMIT {min(limit, 50)}"
    else:
        limit_clause = ""

    # Armar SQL completo
    sql = f"SELECT {select_clause} FROM {from_clause} {where_clause} {group_clause} {order_clause} {limit_clause}"

    return sql, params


# ============================================================================
# Ejecución
# ============================================================================

def execute_report_query(query: dict, tenant_id: str, db) -> dict:
    """
    Ejecuta el query y devuelve datos + metadata.

    Returns:
        {
            "data": [...],          # filas de resultado
            "columns": [...],      # nombres de columnas
            "metric_label": "...", # label de la métrica
            "summary": "...",      # resumen textual
        }
    """
    sql, params = build_sql(query, tenant_id)
    rows = db.execute(text(sql), params).mappings().all()

    data = [dict(r) for r in rows]

    metric = query.get("metric", "count")
    metric_label = METRIC_LABEL.get(metric, "valor")
    intent = query.get("intent", "COUNT")

    # Columnas para tablas
    if data:
        columns = list(data[0].keys())
    else:
        columns = ["grupo", "valor"]

    # Generar resumen textual
    summary = generate_summary(query, data, metric_label)

    return {
        "data": data,
        "columns": columns,
        "metric_label": metric_label,
        "summary": summary,
        "row_count": len(data),
        "sql": sql,
    }


# ============================================================================
# Generación de resumen textual
# ============================================================================

def generate_summary(query: dict, data: list[dict], metric_label: str) -> str:
    """Genera un resumen narrativo del resultado."""
    intent = query.get("intent", "COUNT")
    entity = query.get("entity", "incidente")
    original = query.get("original_text", "")

    if not data:
        return "No se encontraron datos para los filtros especificados."

    if intent == "COUNT":
        if len(data) == 1:
            val = data[0].get("valor", 0)
            return f"Se encontraron {val} {entity}(s) con los filtros especificados."
        else:
            items = ", ".join(f"{r.get('grupo', '?')} ({r.get('valor', 0)})" for r in data[:5])
            return f"Total por grupo: {items}."

    if intent == "AGGREGATE":
        if len(data) == 1:
            val = data[0].get("valor", 0)
            return f"El {metric_label} es {val}."
        else:
            items = ", ".join(f"{r.get('grupo', '?')}: {r.get('valor', 0)}" for r in data[:5])
            return f"{metric_label} por grupo: {items}."

    if intent == "TREND":
        if len(data) >= 2:
            first = data[-1].get("valor", 0)
            last = data[0].get("valor", 0)
            if first and last:
                change = ((last - first) / first * 100) if first else 0
                trend = "aumentó" if change > 0 else "disminuyó"
                return f"La tendencia del {metric_label} {trend} en {abs(change):.1f}% ({len(data)} períodos)."
        return f"Se mostraron {len(data)} puntos en la serie temporal."

    if intent == "RANKING":
        top = data[0]
        return f"El mejor {entity} es '{top.get('grupo', top.get('grupo_nombre', '?'))}' con {top.get('valor', '?')}."

    if intent == "MAP":
        return f"Se encontraron {len(data)} ubicaciones geográficas."

    if intent == "LIST":
        return f"Se encontraron {len(data)} registros."

    if intent == "EXPLAIN":
        return "Se generó un análisis de los datos solicitados."

    return f"Se procesaron {len(data)} resultados."
