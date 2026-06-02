"""Inferencia YOLO: tablero (car-dashboard) + daños (CarDD) → tipo de incidente."""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ml"
MODELS_DIR = ROOT / "models"
CLASSES_DIR = ROOT / "datasets"

# Testigos del tablero → tipo de negocio
DASHBOARD_TO_CODIGO: dict[str, str] = {
    "Charging System Issue": "BATERIA_CARGADOR",
    "Low Fuel": "BATERIA_DESCARGADA",
    "Low Tire Pressure Warning Light": "LLANTA_PRESION",
    "Braking System Issue": "FRENOS",
    "Brake Warning Light": "FRENOS",
    "Check Engine": "MOTOR",
    "Low Engine Oil Warning Light": "MOTOR",
    "Engine Overheating Warning Light": "MOTOR",
    "Electronic Stability Problem -ESP-": "SUSPENSION",
    "Anti Lock Braking System": "FRENOS",
    "Traction Control": "SUSPENSION",
    "SRS-Airbag": "AIRBAG",
    "Master warning light": "MOTOR",
    "Lane Departure": "SUSPENSION",
    "Seat Belt": "AIRBAG",
    "Fog Lamp Indicator": "VIDRIOS_LUCES",
    "Washer Fluid": "OTROS",
    "Auto Shift Lock": "MOTOR",
}

CARDD_TO_CODIGO: dict[str, str] = {
    "tire flat": "LLANTA_PINCHAZO",
    "dent": "COLISION_DENT",
    "scratch": "COLISION_SCRATCH",
    "crack": "COLISION_CRAK",
    "glass shatter": "VIDRIOS_LUCES",
    "lamp broken": "VIDRIOS_LUCES",
}


@lru_cache(maxsize=1)
def _load_models() -> dict:
    try:
        from ultralytics import YOLO
    except ImportError:
        return {}

    models: dict = {}
    dash = MODELS_DIR / "dashboard_best.pt"
    cardd = MODELS_DIR / "cardd_best.pt"
    if dash.exists():
        models["dashboard"] = YOLO(str(dash))
    if cardd.exists():
        models["cardd"] = YOLO(str(cardd))
    return models


def models_available() -> bool:
    return bool(_load_models())


def _map_label(source: str, label: str) -> str:
    label = label.strip()
    if source == "dashboard":
        return DASHBOARD_TO_CODIGO.get(label, "MOTOR")
    if source == "cardd":
        return CARDD_TO_CODIGO.get(label, "COLISION_DENT")
    return "OTROS"


def classify_image_bytes(data: bytes, conf_threshold: float = 0.25) -> tuple[str, float]:
    """Clasifica imagen; devuelve (codigo, confianza)."""
    r = analyze_image_bytes(data, conf_threshold)
    return r["codigo"], r["confianza"]


CODIGO_NOMBRE: dict[str, str] = {
    "BATERIA_CARGADOR": "Problema en cargador/alternador",
    "BATERIA_DESCARGADA": "Batería descargada",
    "LLANTA_PRESION": "Baja presión de llanta",
    "LLANTA_PINCHAZO": "Llanta pinchada",
    "FRENOS": "Falla en sistema de frenos",
    "MOTOR": "Falla en motor",
    "SUSPENSION": "Problema de suspensión/ESP",
    "AIRBAG": "Airbag o cinturón de seguridad",
    "COLISION_DENT": "Abolladura por colisión",
    "COLISION_SCRATCH": "Rayadura/arañazo",
    "COLISION_CRAK": "Grieta o quebrado",
    "VIDRIOS_LUCES": "Vidrio o lampara rota",
    "OTROS": "Problema no clasificado",
}


def prioridad_for_codigo(codigo: str) -> str:
    return {
        "COLISION_DENT": "ALTA",
        "COLISION_SCRATCH": "ALTA",
        "COLISION_CRAK": "ALTA",
        "VIDRIOS_LUCES": "ALTA",
        "MOTOR": "ALTA",
        "FRENOS": "ALTA",
        "AIRBAG": "ALTA",
        "BATERIA_CARGADOR": "MEDIA",
        "BATERIA_DESCARGADA": "MEDIA",
        "LLANTA_PRESION": "MEDIA",
        "LLANTA_PINCHAZO": "MEDIA",
        "SUSPENSION": "MEDIA",
    }.get(codigo, "BAJA")


def build_image_description(codigo: str, etiqueta: str | None, conf: float) -> str:
    tipo = CODIGO_NOMBRE.get(codigo, codigo.lower())
    pct = int(conf * 100)
    if codigo == "OTROS" or conf < 0.35:
        return (
            "No se identificó con claridad el tipo de daño en la foto. "
            "Describa el problema o adjunte otra imagen."
        )
    detalle = f" ({etiqueta})" if etiqueta else ""
    return (
        f"Análisis de imagen (IA): posible emergencia de {tipo}{detalle}, "
        f"confianza {pct}%. Verifique y complete la descripción si hace falta."
    )


def analyze_image_bytes(data: bytes, conf_threshold: float = 0.25) -> dict:
    """CU-18: clasificación + texto sugerido para el conductor."""
    models = _load_models()
    if not models:
        return {
            "codigo": "OTROS",
            "confianza": 0.0,
            "etiqueta": None,
            "fuente": None,
            "descripcion": (
                "Modelos de visión no disponibles. Describa el problema manualmente."
            ),
            "prioridad_sugerida": "BAJA",
            "modelo": "none",
        }

    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    best_codigo, best_conf = "OTROS", 0.0
    best_label: str | None = None
    best_source: str | None = None

    for source, model in models.items():
        results = model.predict(source=img, verbose=False, conf=conf_threshold)
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                name = model.names[cls_id]
                codigo = _map_label(source, name)
                if conf > best_conf:
                    best_codigo, best_conf = codigo, conf
                    best_label = name
                    best_source = source

    if best_conf < 0.35:
        best_codigo, best_conf = "OTROS", max(best_conf, 0.35)

    best_conf = min(best_conf, 0.99)
    return {
        "codigo": best_codigo,
        "confianza": best_conf,
        "etiqueta": best_label,
        "fuente": best_source,
        "descripcion": build_image_description(best_codigo, best_label, best_conf),
        "prioridad_sugerida": prioridad_for_codigo(best_codigo),
        "modelo": "yolov8-dashboard+cardd",
    }


def classify_image_file(path: str | Path) -> tuple[str, float]:
    return classify_image_bytes(Path(path).read_bytes())
