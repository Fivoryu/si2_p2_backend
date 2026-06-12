"""
Generador de datos sintéticos para entrenar el modelo de reportes dinámicos.
Genera pares (texto_nl, query_json) para 8 intents de reportes.

Uso: python generate_training_data.py
Salida: training_data.csv + training_data.json
"""
import json
import csv
import random
import itertools
from pathlib import Path

random.seed(42)

# ============================================================================
# Diccionarios de dominio
# ============================================================================

ENTITIES = {
    "incidente": ["incidentes", "incidentes", "casos", "emergencias", "reportes"],
    "pago": ["pagos", "transacciones", "cobros"],
    "taller": ["talleres", "talleres", "establecimientos"],
    "tecnico": ["técnicos", "técnicos", "auxiliares", "mecánicos"],
    "asignacion": ["asignaciones", "servicios asignados"],
}

ENTITY_SINGULAR = {
    "incidente": "incidente",
    "pago": "pago",
    "taller": "taller",
    "tecnico": "técnico",
    "asignacion": "asignación",
}

TIPO_CODIGO = ["BATERIA", "LLANTA", "MOTOR", "CHOQUE", "OTROS"]
TIPO_NL = {
    "BATERIA": ["de batería", "de batería descargada", "por problema de batería"],
    "LLANTA": ["de llanta", "de llanta pinchada", "por pinchazo", "con flat tire"],
    "MOTOR": ["de motor", "por falla de motor", "de motor dañado"],
    "CHOQUE": ["de choque", "de accidente", "de colisión", "por choque"],
    "OTROS": ["otros", "de otro tipo", "misceláneos", "no clasificados"],
}

ESTADOS_NL = {
    "PENDIENTE": ["pendientes", "en espera", "sin atender"],
    "FINALIZADO": ["finalizados", "completados", "atendidos", "resueltos"],
    "CANCELADO": ["cancelados", "anulados"],
    "EN_ATENCION": ["en atención", "en curso", "activos"],
    "PAGADO": ["pagados", "con pago completado"],
}

PRIORIDADES_NL = {
    "ALTA": ["alta prioridad", "urgentes", "críticos"],
    "MEDIA": ["media prioridad", "normales"],
    "BAJA": ["baja prioridad", "no urgentes"],
}

ZONAS_NL = {
    "norte": ["zona norte", "norte", "del norte"],
    "sur": ["zona sur", "sur", "del sur"],
    "centro": ["centro", "zona central", "del centro"],
    "este": ["zona este", "este", "del este"],
    "oeste": ["zona oeste", "oeste", "del oeste"],
}

TIME_RANGES = {
    "hoy": ["hoy", "el día de hoy", "en el día de hoy", "este día"],
    "ayer": ["ayer", "el día de ayer"],
    "ultima_semana": [
        "la última semana", "esta semana", "en la última semana",
        "de la semana pasada", "en esta semana",
    ],
    "ultimo_mes": [
        "el último mes", "del último mes", "en el último mes",
        "durante el último mes", "del mes pasado", "en junio", "en mayo",
    ],
    "ultimo_trimestre": [
        "el último trimestre", "del último trimestre",
        "en el último trimestre", "en los últimos 3 meses",
    ],
    "ultimo_anio": [
        "el último año", "del último año", "en el último año",
        "durante el año", "en 2026", "en 2025",
    ],
}

METRICS_NL = {
    "avg_llegada": [
        "tiempo promedio de llegada", "tiempo promedio en llegar",
        "tiempo medio de llegada", "cuánto tardan en llegar",
        "tiempo promedio de respuesta", "velocidad de respuesta",
    ],
    "avg_asignacion": [
        "tiempo promedio de asignación", "tiempo promedio para asignar",
        "cuánto tarda en asignar", "tiempo medio de asignación",
    ],
    "avg_calificacion": [
        "calificación promedio", "rating promedio", "puntuación promedio",
        "calificación media", "qué tan bien calificados están",
    ],
    "tasa_rechazo": [
        "tasa de rechazo", "porcentaje de rechazos",
        "cuántos rechazan", "razón de rechazo", "cuántos rechazan los servicios",
    ],
    "total_ingresos": [
        "ingresos totales", "total de ingresos", "cuánto se ha facturado",
        "facturación total", "cuánto dinero ha ingresado", "ganancias",
    ],
    "total_comisiones": [
        "comisiones totales", "comisión de la plataforma",
        "cuánto gana la plataforma", "comisiones cobradas",
    ],
    "count": [
        "total", "cantidad", "cuántos", "cuántas", "número de",
    ],
    "sum_monto": [
        "monto total", "suma de montos", "total pagado", "cuánto se ha pagado",
    ],
}

GROUP_BY_NL = {
    "taller_id": ["por taller", "de cada taller", "por establecimiento"],
    "tipo_codigo": ["por tipo", "por tipo de incidente", "por categoría"],
    "zona": ["por zona", "por ubicación", "por geolocalización"],
    "dia": ["por día", "diariamente", "cada día"],
    "semana": ["por semana", "semanalmente", "cada semana"],
    "mes": ["por mes", "mensualmente", "cada mes"],
    "hora": ["por hora", "por horario", "cada hora"],
    "estado": ["por estado", "por estatus"],
}

ORDER_NL = {
    "desc": ["mayor", "más alto", "más grande", "el top", "los más"],
    "asc": ["menor", "más bajo", "más bajo", "los peores"],
}

LIMITS = [5, 10, 15, 20, 25, 50]

# ============================================================================
# Plantillas por intent
# ============================================================================

# COUNT: ¿Cuántos X [filtro] [tiempo]?
COUNT_TEMPLATES = [
    "¿Cuántos {entity} {filters} {time}?",
    "¿Cuántos {entity} {time} {filters}?",
    "¿Cuántas {entity} {filters} hubo {time}?",
    "Dime el total de {entity} {filters} {time}",
    "Cuéntame cuántos {entity} {filters} {time}",
    "¿Qué cantidad de {entity} {filters} {time}?",
    "Necesito saber el número de {entity} {filters} {time}",
    "¿Cuántos {entity} hubo en total {time}?",
    "El total de {entity} {time}",
    "¿Cuál es la cantidad de {entity} {time}?",
    "¿Cuántos {entity} registraron {time}?",
    "Dame el conteo de {entity} {filters} {time}",
]

# AGGREGATE: Promedio/suma de métrica [por grupo] [filtro] [tiempo]
AGGREGATE_TEMPLATES = [
    "¿Cuál es el {metric} de los {entity} {filters} {time}?",
    "Dime el {metric} de los {entity} {filters}",
    "¿Qué {metric} tienen los {entity} {time}?",
    "Calcula el {metric} de {entity} {filters} {time}",
    "¿Cuál es el {metric} {group} {time}?",
    "Necesito saber el {metric} {group} {filters} {time}",
    "El {metric} de los {entity} {time}",
    "¿Qué {metric} se registra {group} {time}?",
    "¿Cómo está el {metric} de los {entity} {filters}?",
    "Muéstrame el {metric} {filters} {time}",
]

# TREND: Evolución de métrica [por periodo] [filtro]
TREND_TEMPLATES = [
    "¿Cómo evolucionó el {metric} de los {entity} {time}?",
    "Muéstrame la tendencia de {entity} {time}",
    "¿Cómo ha cambiado el {metric} {time}?",
    "Gráfica de tendencia de {entity} {filters} {time}",
    "¿Cuál es la evolución de {entity} {time}?",
    "Dame la evolución del {metric} {group} {time}",
    "¿Cómo va el {metric} {filters} este mes?",
    "Tendencia de {entity} {filters} en el tiempo",
    "¿Qué tendencia muestran los {entity} {time}?",
    "Evolución temporal de {entity} {filters}",
    "¿Cómo se comportó el {metric} {time}?",
    "Gráfico de líneas de {entity} {time}",
]

# RANKING: Top N de X por métrica
RANKING_TEMPLATES = [
    "¿Cuáles son los {limit} {entity} con mejor {metric}?",
    "Dame el ranking de {entity} por {metric}",
    "¿Quiénes son los mejores {entity} en {metric}?",
    "Top {limit} {entity} {filters} por {metric}",
    "Los {entity} con mayor {metric}",
    "¿Cuáles son los {entity} {filters} con más {metric}?",
    "Ranking de {entity} {filters} por {metric}",
    "Los {limit} {entity} mejor {metric}",
    "¿Cuáles lideran en {metric} entre los {entity}?",
    "Ordéname los {entity} por {metric} de mayor a menor",
]

# COMPARE: Comparar X vs Y en métrica
COMPARE_TEMPLATES = [
    "Compara el {metric} entre {entity_a} y {entity_b}",
    "¿Cuál es la diferencia de {metric} entre {entity_a} y {entity_b}?",
    "¿Cómo se comparan {entity_a} y {entity_b} en {metric}?",
    "Comparativa de {metric} entre {entity_a} y {entity_b}",
    "¿Quién tiene mejor {metric}, {entity_a} o {entity_b}?",
    "Dime las diferencias de {metric} entre {entity_a} y {entity_b}",
]

# MAP: Ubicación geográfica de entidades
MAP_TEMPLATES = [
    "Muéstrame los {entity} {filters} en el mapa",
    "¿Dónde están los {entity} {filters}?",
    "Mapa de {entity} {filters}",
    "Ubicación de los {entity} {filters}",
    "Dibuja los {entity} {filters} en un mapa",
    "¿En qué zonas hay más {entity} {filters}?",
    "Muéstrame en un mapa los {entity} {time}",
    "Mapa de calor de {entity} {filters} {time}",
    "¿Cómo se distribuyen geográficamente los {entity} {filters}?",
]

# LIST: Listar entidades con filtros
LIST_TEMPLATES = [
    "Lista los {entity} {filters} {time}",
    "Muéstrame todos los {entity} {filters}",
    "Dame el listado de {entity} {filters} {time}",
    "¿Qué {entity} {filters} {time} hay?",
    "Ver {entity} {filters}",
    "Necesito ver los {entity} {filters} {time}",
    "Muéstrame la lista de {entity} {filters}",
    "¿Puedo ver los {entity} {filters} {time}?",
]

# EXPLAIN: Explicar tendencia o métrica
EXPLAIN_TEMPLATES = [
    "¿Por qué aumentaron los {entity} {time}?",
    "¿Qué causó el cambio en {metric} {time}?",
    "Explica el comportamiento de {entity} {time}",
    "¿Por qué el {metric} {filters} está así?",
    "¿Cuál es la razón del aumento de {entity} {time}?",
    "Analiza por qué {metric} {filters} {time}",
    "¿Qué factores influyen en el {metric} de los {entity}?",
    "Explícame los {entity} {time}",
]


# ============================================================================
# Generadores de texto por intent
# ============================================================================

def pick(lst):
    return random.choice(lst)


def generate_filters(entity):
    """Genera filtros aleatorios en NL y su equivalente JSON."""
    filters_nl = ""
    filters_json = {}
    options = ["tipo", "estado", "prioridad", "zona", "none"]
    weights = [0.3, 0.2, 0.15, 0.15, 0.2]
    choice = random.choices(options, weights=weights, k=1)[0]

    if choice == "tipo":
        tipo = pick(TIPO_CODIGO)
        filters_nl = pick(TIPO_NL[tipo])
        filters_json["tipo_codigo"] = tipo
    elif choice == "estado" and entity == "incidente":
        estado = pick(list(ESTADOS_NL.keys()))
        filters_nl = pick(ESTADOS_NL[estado])
        filters_json["estado"] = estado
    elif choice == "prioridad" and entity == "incidente":
        prio = pick(list(PRIORIDADES_NL.keys()))
        filters_nl = pick(PRIORIDADES_NL[prio])
        filters_json["prioridad"] = prio
    elif choice == "zona":
        zona = pick(list(ZONAS_NL.keys()))
        filters_nl = pick(ZONAS_NL[zona])
        filters_json["zona"] = zona

    return filters_nl, filters_json


def generate_time_range():
    """Genera rango temporal en NL y su equivalente JSON."""
    key = pick(list(TIME_RANGES.keys()))
    return pick(TIME_RANGES[key]), key


def generate_metric():
    """Genera métrica en NL y su equivalente JSON."""
    key = pick(list(METRICS_NL.keys()))
    return pick(METRICS_NL[key]), key


def generate_group_by():
    """Genera agrupación en NL y su equivalente JSON."""
    key = pick(list(GROUP_BY_NL.keys()))
    return pick(GROUP_BY_NL[key]), key


# ============================================================================
# Generadores principales
# ============================================================================

def gen_count():
    entity_key = pick(list(ENTITIES.keys()))
    entity_nl = pick(ENTITIES[entity_key])
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()

    tmpl = pick(COUNT_TEMPLATES)
    text = tmpl.format(entity=entity_nl, filters=filters_nl, time=time_nl)
    text = _clean(text)

    query = {
        "intent": "COUNT",
        "entity": entity_key,
        "metric": "count",
        "group_by": "none",
        "filters": filters_json,
        "time_range": time_key,
        "visualization": "kpi_card",
    }
    return text, query


def gen_aggregate():
    entity_key = pick(["incidente", "taller", "pago", "tecnico"])
    entity_nl = pick(ENTITIES[entity_key])
    metric_nl, metric_key = generate_metric()
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()
    group_nl, group_key = random.choice(
        [(g, k) for k, gl in GROUP_BY_NL.items() for g in gl]
        + [("none", "none")]
    )

    tmpl = pick(AGGREGATE_TEMPLATES)
    text = tmpl.format(
        metric=metric_nl, entity=entity_nl,
        filters=filters_nl, time=time_nl,
        group=group_nl if group_nl != "none" else "",
    )
    text = _clean(text)

    viz = "bar" if group_key != "none" else "kpi_card"
    query = {
        "intent": "AGGREGATE",
        "entity": entity_key,
        "metric": metric_key,
        "group_by": group_key if group_key != "none" else "none",
        "filters": filters_json,
        "time_range": time_key,
        "visualization": viz,
    }
    return text, query


def gen_trend():
    entity_key = pick(["incidente", "pago", "asignacion"])
    entity_nl = pick(ENTITIES[entity_key])
    metric_nl, metric_key = generate_metric()
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()

    tmpl = pick(TREND_TEMPLATES)
    text = tmpl.format(
        metric=metric_nl, entity=entity_nl,
        filters=filters_nl, time=time_nl,
        group="",
    )
    text = _clean(text)

    query = {
        "intent": "TREND",
        "entity": entity_key,
        "metric": metric_key,
        "group_by": "time",
        "filters": filters_json,
        "time_range": time_key,
        "visualization": "line",
    }
    return text, query


def gen_ranking():
    entity_key = pick(["taller", "tecnico", "incidente"])
    entity_nl = pick(ENTITIES[entity_key])
    metric_nl, metric_key = generate_metric()
    filters_nl, filters_json = generate_filters(entity_key)
    limit = pick(LIMITS)

    tmpl = pick(RANKING_TEMPLATES)
    text = tmpl.format(
        metric=metric_nl, entity=entity_nl,
        filters=filters_nl, limit=limit,
    )
    text = _clean(text)

    query = {
        "intent": "RANKING",
        "entity": entity_key,
        "metric": metric_key,
        "group_by": "none",
        "filters": filters_json,
        "time_range": "none",
        "limit": limit,
        "order": "desc",
        "visualization": "horizontal_bar",
    }
    return text, query


def gen_compare():
    entity_key = "taller"
    entity_nl = pick(ENTITIES[entity_key])
    metric_nl, metric_key = generate_metric()
    entity_a = f"taller {pick(['A', 'B', 'Norte', 'Sur', 'Rápido', 'Lento', 'Juan', 'Pedro'])}"
    entity_b = f"taller {pick(['B', 'C', 'Este', 'Oeste', 'Lento', 'Rápido', 'María', 'Ana'])}"

    tmpl = pick(COMPARE_TEMPLATES)
    text = tmpl.format(
        metric=metric_nl, entity_a=entity_a, entity_b=entity_b,
    )
    text = _clean(text)

    query = {
        "intent": "COMPARE",
        "entity": entity_key,
        "metric": metric_key,
        "group_by": "none",
        "filters": {},
        "time_range": "none",
        "compare": [entity_a, entity_b],
        "visualization": "bar",
    }
    return text, query


def gen_map():
    entity_key = pick(["incidente", "taller"])
    entity_nl = pick(ENTITIES[entity_key])
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()

    tmpl = pick(MAP_TEMPLATES)
    text = tmpl.format(entity=entity_nl, filters=filters_nl, time=time_nl)
    text = _clean(text)

    query = {
        "intent": "MAP",
        "entity": entity_key,
        "metric": "count",
        "group_by": "zona",
        "filters": filters_json,
        "time_range": time_key,
        "visualization": "map",
    }
    return text, query


def gen_list():
    entity_key = pick(list(ENTITIES.keys()))
    entity_nl = pick(ENTITIES[entity_key])
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()
    limit = pick([10, 20, 50, 100])

    tmpl = pick(LIST_TEMPLATES)
    text = tmpl.format(entity=entity_nl, filters=filters_nl, time=time_nl)
    text = _clean(text)

    query = {
        "intent": "LIST",
        "entity": entity_key,
        "metric": "none",
        "group_by": "none",
        "filters": filters_json,
        "time_range": time_key,
        "limit": limit,
        "visualization": "table",
    }
    return text, query


def gen_explain():
    entity_key = pick(["incidente", "pago", "asignacion"])
    entity_nl = pick(ENTITIES[entity_key])
    metric_nl, metric_key = generate_metric()
    filters_nl, filters_json = generate_filters(entity_key)
    time_nl, time_key = generate_time_range()

    tmpl = pick(EXPLAIN_TEMPLATES)
    text = tmpl.format(
        metric=metric_nl, entity=entity_nl,
        filters=filters_nl, time=time_nl,
    )
    text = _clean(text)

    query = {
        "intent": "EXPLAIN",
        "entity": entity_key,
        "metric": metric_key,
        "group_by": "none",
        "filters": filters_json,
        "time_range": time_key,
        "visualization": "text",
    }
    return text, query


# ============================================================================
# Helpers
# ============================================================================

def _clean(text):
    """Limpia espacios múltiples y asegura terminación con ?."""
    text = " ".join(text.split())
    if not text.endswith("?") and not text.endswith("."):
        text += "?"
    return text.capitalize()


GENERATORS = [
    ("COUNT", gen_count, 120),
    ("AGGREGATE", gen_aggregate, 140),
    ("TREND", gen_trend, 120),
    ("RANKING", gen_ranking, 100),
    ("COMPARE", gen_compare, 60),
    ("MAP", gen_map, 100),
    ("LIST", gen_list, 100),
    ("EXPLAIN", gen_explain, 60),
]


def main():
    out_dir = Path(__file__).parent
    records = []

    for intent_name, gen_fn, count in GENERATORS:
        for _ in range(count):
            text, query = gen_fn()
            records.append({
                "text": text,
                "intent": intent_name,
                "query_json": json.dumps(query, ensure_ascii=False),
            })

    random.shuffle(records)

    # CSV
    csv_path = out_dir / "training_data.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "intent", "query_json"])
        writer.writeheader()
        writer.writerows(records)

    # JSON (más cómodo para inspección)
    json_path = out_dir / "training_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # Stats
    intents = {}
    for r in records:
        intents[r["intent"]] = intents.get(r["intent"], 0) + 1

    print(f"Total de ejemplos generados: {len(records)}")
    print(f"Por intent:")
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count}")
    print(f"\nArchivos guardados:")
    print(f"  {csv_path}")
    print(f"  {json_path}")


if __name__ == "__main__":
    main()
