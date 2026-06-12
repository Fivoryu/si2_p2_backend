from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PricingResult:
    precio_sugerido: float
    tiempo_llegada_min: int
    tiempo_reparacion_min: int
    tiempo_total_min: int
    dificultad: str
    precio_min: float = 0.0
    precio_max: float = 0.0
    ajuste_demanda: float = 0.0
    ajuste_calidad: float = 0.0
    ajuste_distancia: float = 0.0
    ajuste_prioridad: float = 0.0
    comision_plataforma: float = 0.0
    monto_taller: float = 0.0


BASE_COST = {
    "BATERIA": 80.0,
    "LLANTA": 70.0,
    "MOTOR": 180.0,
    "CHOQUE": 250.0,
    "OTROS": 120.0,
}

BASE_REPAIR_MIN = {
    "BATERIA": 25,
    "LLANTA": 35,
    "MOTOR": 90,
    "CHOQUE": 120,
    "OTROS": 60,
}

DIFFICULTY_FACTOR = {
    "BAJA": 0.85,
    "MEDIA": 1.0,
    "ALTA": 1.35,
}


def difficulty_for(tipo_codigo: str | None, prioridad: str | None) -> str:
    if prioridad == "ALTA" or tipo_codigo in {"MOTOR", "CHOQUE"}:
        return "ALTA"
    if prioridad == "BAJA" or tipo_codigo == "OTROS":
        return "BAJA"
    return "MEDIA"


def calculate_service_offer(
    tipo_codigo: str | None,
    prioridad: str | None,
    distancia_km: float | None,
    calificacion: float | None,
    carga: int | None = 0,
    demanda_activa: int | None = 0,
    talleres_disponibles: int | None = None,
    eficiencia: float | None = None,
    cumplimiento_sla: float | None = None,
    rechazo_penalty: float | None = None,
    hora_pico: bool = False,
) -> PricingResult:
    codigo = tipo_codigo or "OTROS"
    distancia = max(float(distancia_km or 0), 0.0)
    rating = min(max(float(calificacion or 3.0), 0.0), 5.0)
    dificultad = difficulty_for(codigo, prioridad)
    factor = DIFFICULTY_FACTOR[dificultad]

    base = BASE_COST.get(codigo, BASE_COST["OTROS"])
    demanda = max(int(demanda_activa or 0), 0)
    disponibles = max(int(talleres_disponibles or 1), 1)
    eff = min(max(float(eficiencia if eficiencia is not None else 0.75), 0.0), 1.0)
    sla = min(max(float(cumplimiento_sla if cumplimiento_sla is not None else 0.75), 0.0), 1.0)
    rechazo = min(max(float(rechazo_penalty or 0.0), 0.0), 1.0)

    distancia_cost = distancia * 4.5
    prioridad_cost = base * 0.35 if prioridad == "ALTA" else base * 0.12 if prioridad == "MEDIA" else 0.0
    demanda_factor = min(0.45, demanda / (disponibles * 12.0))
    demanda_cost = base * demanda_factor + (base * 0.10 if hora_pico else 0.0)
    quality_factor = max(rating - 3.0, 0.0) * 0.04 + eff * 0.04 + sla * 0.04
    quality_cost = base * quality_factor
    rechazo_discount = base * rechazo * 0.08
    precio = (base * factor) + distancia_cost + prioridad_cost + demanda_cost + quality_cost - rechazo_discount

    llegada = int(round((distancia / 28.0) * 60.0 + 5.0))
    reparacion = int(round(BASE_REPAIR_MIN.get(codigo, 60) * factor + int(carga or 0) * 8))
    total = max(llegada + reparacion, 1)
    precio = max(precio, base * 0.65)
    precio_sugerido = round(precio, 2)
    comision = round(precio_sugerido * 0.10, 2)
    return PricingResult(
        precio_sugerido=precio_sugerido,
        tiempo_llegada_min=max(llegada, 1),
        tiempo_reparacion_min=max(reparacion, 1),
        tiempo_total_min=total,
        dificultad=dificultad,
        precio_min=round(precio_sugerido * 0.85, 2),
        precio_max=round(precio_sugerido * 1.25, 2),
        ajuste_demanda=round(demanda_cost, 2),
        ajuste_calidad=round(quality_cost, 2),
        ajuste_distancia=round(distancia_cost, 2),
        ajuste_prioridad=round(prioridad_cost, 2),
        comision_plataforma=comision,
        monto_taller=round(precio_sugerido - comision, 2),
    )


def pricing_as_dict(
    tipo_codigo: str | None,
    prioridad: str | None,
    distancia_km: float | None,
    calificacion: float | None,
    carga: int | None = 0,
) -> dict:
    """Precio dinámico para UI (CU27): base + distancia + prioridad + demanda + calidad."""
    p = calculate_service_offer(
        tipo_codigo,
        prioridad,
        distancia_km,
        calificacion,
        carga,
    )
    return {
        "precio_sugerido": p.precio_sugerido,
        "precio_min": p.precio_min,
        "precio_max": p.precio_max,
        "tiempo_llegada_min": p.tiempo_llegada_min,
        "tiempo_total_min": p.tiempo_total_min,
        "dificultad": p.dificultad,
        "comision_plataforma": p.comision_plataforma,
        "monto_taller": p.monto_taller,
        "ajuste_distancia": p.ajuste_distancia,
        "ajuste_prioridad": p.ajuste_prioridad,
        "ajuste_demanda": p.ajuste_demanda,
        "ajuste_calidad": p.ajuste_calidad,
    }
