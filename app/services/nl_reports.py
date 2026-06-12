"""
Parser de lenguaje natural para reportes dinámicos.

Pipeline: texto NL → TF-IDF → Modelo TF → Intent + Entidades → Query JSON.

Uso:
    from app.services.nl_reports import parse_nl_query
    result = parse_nl_query("¿Cuántos incidentes de batería hubo en junio?")
    # → {"intent": "COUNT", "entity": "incidente", "filters": {"tipo_codigo": "BATERIA"}, "time_range": "ultimo_mes"}
"""
import json
import os
import pickle
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

# Rutas a los artefactos del modelo
_MODEL_DIR = Path(os.environ.get(
    "REPORT_MODEL_DIR",
    Path(__file__).resolve().parent.parent.parent / "ml" / "reportes",
))

# ============================================================================
# Diccionarios de dominio para extracción de entidades (regex-based NER)
# ============================================================================

ENTITY_KEYWORDS = {
    "incidente": [
        "incidente", "incidentes", "caso", "casos", "emergencia", "emergencias",
        "reporte", "reportes", "solicitud", "solicitudes", "pedido",
    ],
    "pago": [
        "pago", "pagos", "transacción", "transacciones", "cobro", "cobros",
        "factura", "facturas", "monto", "monto total",
    ],
    "taller": [
        "taller", "talleres", "establecimiento", "establecimientos",
    ],
    "tecnico": [
        "técnico", "técnicos", "auxiliar", "auxiliares", "mecánico", "mecánicos",
        "operador",
    ],
    "asignacion": [
        "asignación", "asignaciones", "servicio asignado", "servicios asignados",
    ],
}

METRIC_KEYWORDS = {
    "avg_llegada": [
        "tiempo promedio de llegada", "tiempo promedio en llegar",
        "tiempo medio de llegada", "cuánto tardan en llegar",
        "tiempo promedio de respuesta", "velocidad de respuesta",
        "promedio de llegada", "promedio llegada",
    ],
    "avg_asignacion": [
        "tiempo promedio de asignación", "tiempo promedio para asignar",
        "cuánto tarda en asignar", "tiempo medio de asignación",
        "promedio de asignación", "promedio asignación",
    ],
    "avg_calificacion": [
        "calificación promedio", "rating promedio", "puntuación promedio",
        "calificación media", "promedio de calificación", "mejor calificación",
        "calificación", "rating",
    ],
    "tasa_rechazo": [
        "tasa de rechazo", "porcentaje de rechazos", "cuántos rechazan",
        "razón de rechazo", "ratio de rechazo",
    ],
    "total_ingresos": [
        "ingresos totales", "total de ingresos", "cuánto se ha facturado",
        "facturación total", "ganancias",
    ],
    "total_comisiones": [
        "comisiones totales", "comisión de la plataforma",
        "cuánto gana la plataforma",
    ],
    "count": [
        "total", "cantidad", "cuántos", "cuántas", "número de", "número",
    ],
    "sum_monto": [
        "monto total", "suma de montos", "total pagado", "cuánto se ha pagado",
    ],
}

GRANULARITY_KEYWORDS = {
    "hora": ["por hora", "por horario", "cada hora", "hora"],
    "dia": ["por día", "diariamente", "cada día", "día"],
    "semana": ["por semana", "semanalmente", "cada semana", "semana"],
    "mes": ["por mes", "mensualmente", "cada mes", "mes"],
}

TIME_RANGES = {
    "hoy": ["hoy", "el día de hoy", "este día"],
    "ayer": ["ayer", "el día de ayer"],
    "ultima_semana": [
        "la última semana", "esta semana", "la semana pasada", "esta semana",
    ],
    "ultimo_mes": [
        "el último mes", "del mes pasado", "del último mes",
        "en junio", "en mayo", "en julio", "en agosto", "en marzo",
        "en abril", "en enero", "en febrero", "en septiembre",
        "en octubre", "en noviembre", "en diciembre",
    ],
    "ultimo_trimestre": [
        "el último trimestre", "los últimos 3 meses", "del último trimestre",
    ],
    "ultimo_anio": [
        "el último año", "del último año", "en 2026", "en 2025",
    ],
}

TIPO_KEYWORDS = {
    "BATERIA": ["batería", "bateria", "battery", "cargador", "descargada"],
    "LLANTA": ["llanta", "llantas", "pinchazo", "neumático", "flat", "rueda"],
    "MOTOR": ["motor", "falla de motor", "sobrecalentamiento"],
    "CHOQUE": ["choque", "accidente", "colisión", "collision"],
    "OTROS": ["otros", "otro tipo", "misceláneo", "no clasificado"],
}

ESTADO_KEYWORDS = {
    "PENDIENTE": ["pendiente", "pendientes", "en espera", "sin atender"],
    "FINALIZADO": ["finalizado", "finalizados", "completado", "completados", "atendidos", "resueltos"],
    "CANCELADO": ["cancelado", "cancelados", "anulados"],
    "EN_ATENCION": ["en atención", "en curso", "activos"],
    "PAGADO": ["pagado", "pagados"],
}

PRIORIDAD_KEYWORDS = {
    "ALTA": ["alta prioridad", "urgente", "urgentes", "crítico", "críticos", "alta"],
    "MEDIA": ["media prioridad", "normal", "normales"],
    "BAJA": ["baja prioridad", "no urgente", "no urgentes", "baja"],
}

ZONA_KEYWORDS = {
    "norte": ["zona norte", "norte", "del norte"],
    "sur": ["zona sur", "sur", "del sur"],
    "centro": ["centro", "zona central", "del centro"],
    "este": ["zona este", "este", "del este"],
    "oeste": ["zona oeste", "oeste", "del oeste"],
}

ORDER_KEYWORDS = {
    "desc": ["mayor", "más alto", "más grande", "top", "mejor"],
    "asc": ["menor", "más bajo", "peor", "peores"],
}


# ============================================================================
# Funciones de extracción de entidades
# ============================================================================

def _extract_first_match(text: str, dictionary: dict) -> str | None:
    """Devuelve la primera key cuyo valor aparece en el texto."""
    t = text.lower()
    for key, keywords in dictionary.items():
        for kw in keywords:
            if kw in t:
                return key
    return None


def _extract_limit(text: str) -> int | None:
    """Extrae un número de límite (top N) del texto."""
    patterns = [
        r"top\s+(\d+)",
        r"los\s+(\d+)\s+(?:mejores|peores|más|menos)",
        r"primeros?\s+(\d+)",
        r"(\d+)\s+(?:mejores|peores|más|mejor)",
    ]
    for p in patterns:
        m = re.search(p, text.lower())
        if m:
            return int(m.group(1))
    return None


def _extract_metric(text: str) -> str:
    """Extrae la métrica del texto."""
    t = text.lower()
    # Buscar coincidencias más largas primero (bigrams/trigrams)
    best = "count"
    best_len = 0
    for metric, keywords in METRIC_KEYWORDS.items():
        for kw in keywords:
            if kw in t and len(kw) > best_len:
                best = metric
                best_len = len(kw)
    return best


def _extract_time_range(text: str) -> str:
    """Extrae el rango temporal del texto."""
    t = text.lower()
    for key, keywords in TIME_RANGES.items():
        for kw in keywords:
            if kw in t:
                return key
    return "none"


def _extract_granularity(text: str) -> str:
    """Extrae la granularidad temporal para trends."""
    t = text.lower()
    for key, keywords in GRANULARITY_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                return key
    return "none"


def _should_use_map(text: str) -> bool:
    """Detecta si el usuario quiere un mapa."""
    t = text.lower()
    return any(w in t for w in ["mapa", "map", "ubicación", "dónde", "geográfico", "heatmap"])


def _should_use_list(text: str) -> bool:
    """Detecta si el usuario quiere una lista."""
    t = text.lower()
    return any(w in t for w in ["lista", "listar", "listado", "ver todos", "muéstrame todos"])


def _is_compare(text: str) -> bool:
    """Detecta si el usuario quiere comparar."""
    t = text.lower()
    return any(w in t for w in ["compara", "comparar", "vs", "versus", "diferencia entre"])


def _is_explain(text: str) -> bool:
    """Detecta si el usuario quiere una explicación."""
    t = text.lower()
    return any(w in t for w in ["por qué", "por que", "explica", "analiza", "razón", "causa", "motivo"])


def _extract_compare_entities(text: str) -> list[str]:
    """Extrae las dos entidades a comparar."""
    t = text.lower()
    patterns = [
        r"entre\s+['\"]?([^'\"yo]+?)['\"]?\s+y\s+['\"]?([^'\"yo]+?)['\"]",
        r"compar[ae]\s+['\"]?([^'\"yo]+?)['\"]?\s+con\s+['\"]?([^'\"yo]+?)['\"]",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            return [m.group(1).strip(), m.group(2).strip()]
    return []


# ============================================================================
# Función principal
# ============================================================================

@lru_cache(maxsize=1)
def _load_model():
    """Carga modelo TF, vectorizer y label encoder (una sola vez)."""
    import tensorflow as tf

    model_path = _MODEL_DIR / "intent_model.h5"
    tfidf_path = _MODEL_DIR / "tfidf_vectorizer.pkl"
    le_path = _MODEL_DIR / "label_encoder.pkl"

    if not all(p.exists() for p in [model_path, tfidf_path, le_path]):
        raise FileNotFoundError(
            f"Modelos no encontrados en {_MODEL_DIR}. "
            "Ejecuta python ml/reportes/train_report_model.py primero."
        )

    model = tf.keras.models.load_model(str(model_path))
    with open(tfidf_path, "rb") as f:
        tfidf = pickle.load(f)
    with open(le_path, "rb") as f:
        le = pickle.load(f)

    return model, tfidf, le


def parse_nl_query(texto: str) -> dict:
    """
    Convierte una pregunta en lenguaje natural a un query estructurado.

    Returns:
        {
            "intent": "COUNT|AGGREGATE|TREND|RANKING|COMPARE|MAP|LIST|EXPLAIN",
            "entity": "incidente|pago|taller|tecnico|asignacion",
            "metric": "count|avg_llegada|avg_asignacion|...",
            "group_by": "taller_id|tipo_codigo|zona|dia|semana|mes|hora|none",
            "filters": {"tipo_codigo": "BATERIA", "estado": "FINALIZADO", ...},
            "time_range": "hoy|ayer|ultima_semana|...",
            "limit": 10,
            "order": "asc|desc",
            "visualization": "bar|line|pie|map|table|kpi_card|...",
            "compare": ["entidad_a", "entidad_b"],  // solo para COMPARE
        }
    """
    texto_lower = texto.lower()

    # 1. Predecir intent con el modelo TF
    model, tfidf, le = _load_model()
    X = tfidf.transform([texto]).toarray()
    y_pred = model.predict(X, verbose=0)
    intent_idx = int(np.argmax(y_pred[0]))
    intent = le.classes_[intent_idx]
    confidence = float(y_pred[0][intent_idx])

    # 2. Extraer entidades con regex + diccionarios de dominio
    entity = _extract_first_match(texto, ENTITY_KEYWORDS) or "incidente"
    metric = _extract_metric(texto)
    time_range = _extract_time_range(texto)
    granularity = _extract_granularity(texto)

    # Filtros
    filters = {}
    tipo = _extract_first_match(texto, TIPO_KEYWORDS)
    if tipo:
        filters["tipo_codigo"] = tipo

    estado = _extract_first_match(texto, ESTADO_KEYWORDS)
    if estado:
        filters["estado"] = estado

    prioridad = _extract_first_match(texto, PRIORIDAD_KEYWORDS)
    if prioridad:
        filters["prioridad"] = prioridad

    zona = _extract_first_match(texto, ZONA_KEYWORDS)
    if zona:
        filters["zona"] = zona

    # 3. Overrides basados en reglas (más confiables que el modelo)
    if _is_explain(texto):
        intent = "EXPLAIN"
    elif _should_use_map(texto):
        intent = "MAP"
    elif _is_compare(texto):
        intent = "COMPARE"
    elif _should_use_list(texto):
        intent = "LIST"

    # 4. Group by
    group_by = "none"
    if granularity != "none":
        group_by = granularity
    else:
        gb = _extract_first_match(texto, {
            "taller_id": ["por taller", "de cada taller"],
            "tipo_codigo": ["por tipo", "por categoría"],
            "zona": ["por zona", "por ubicación"],
            "dia": ["por día", "diariamente"],
            "semana": ["por semana", "semanalmente"],
            "mes": ["por mes", "mensualmente"],
            "hora": ["por hora", "por horario"],
            "estado": ["por estado", "por estatus"],
        })
        if gb:
            group_by = gb

    # 5. Limit
    limit = _extract_limit(texto) or 20

    # 6. Order
    order = _extract_first_match(texto, ORDER_KEYWORDS) or "desc"

    # 7. Compare
    compare = []
    if intent == "COMPARE":
        compare = _extract_compare_entities(texto)
        if len(compare) < 2:
            compare = ["taller A", "taller B"]

    # 8. Visualización recomendada
    viz = _recommend_viz(intent, entity, group_by, metric, filters)

    return {
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "group_by": group_by,
        "filters": filters,
        "time_range": time_range,
        "limit": limit,
        "order": order,
        "visualization": viz,
        "compare": compare,
        "confidence": round(confidence, 3),
        "original_text": texto,
    }


def _recommend_viz(intent: str, entity: str, group_by: str, metric: str, filters: dict) -> str:
    """Recomienda el tipo de visualización."""
    if intent == "MAP":
        return "map"
    if intent == "LIST":
        return "table"
    if intent == "COMPARE":
        return "bar"
    if intent == "TREND":
        return "line"
    if intent == "EXPLAIN":
        return "text"
    if intent == "RANKING":
        return "horizontal_bar"
    if intent == "COUNT" and group_by == "none":
        return "kpi_card"
    if intent == "AGGREGATE":
        if group_by != "none":
            return "bar"
        return "kpi_card"
    # COUNT con group_by → bar
    if group_by != "none":
        return "bar"
    return "kpi_card"
