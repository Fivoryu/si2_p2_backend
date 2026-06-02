import asyncio
from sqlalchemy import text

from ..core.aws import download_bytes
from ..core.db import scoped_session
from .assignment import assign_best_workshop
from .vision import classify_image_bytes, models_available

TIPOS = {
    "BATERIA_CARGADOR": "33333333-0000-0000-0000-000000000001",
    "BATERIA_DESCARGADA": "33333333-0000-0000-0000-000000000002",
    "LLANTA_PINCHAZO": "33333333-0000-0000-0000-000000000003",
    "MOTOR": "33333333-0000-0000-0000-000000000004",
    "OTROS": "33333333-0000-0000-0000-000000000005",
    "LLANTA_PRESION": "33333333-0000-0000-0000-000000000006",
    "FRENOS": "33333333-0000-0000-0000-000000000007",
    "SUSPENSION": "33333333-0000-0000-0000-000000000008",
    "AIRBAG": "33333333-0000-0000-0000-000000000009",
    "COLISION_DENT": "33333333-0000-0000-0000-000000000010",
    "COLISION_SCRATCH": "33333333-0000-0000-0000-000000000011",
    "COLISION_CRAK": "33333333-0000-0000-0000-000000000012",
    "VIDRIOS_LUCES": "33333333-0000-0000-0000-000000000013",
}

KEYWORDS = {
    "BATERIA_CARGADOR": [
        "cargador",
        "alternador",
        "no carga",
        "battery",
        "weak lights",
        "lights are weak",
        "wont start",
        "won't start",
    ],
    "BATERIA_DESCARGADA": ["bateria", "descargada", "arranque", "no enciende"],
    "LLANTA_PRESION": ["presion", "inflar", "baja presion"],
    "LLANTA_PINCHAZO": ["llanta", "pinchazo", "neumático", "rueda", "flat"],
    "FRENOS": ["frenos", "freno", "brake", "sistema frenado"],
    "MOTOR": ["motor", "humo", "sobrecalent", "falla mecanica", "engine"],
    "SUSPENSION": ["suspension", "amortigu", "ESP"],
    "AIRBAG": ["airbag", "srs", "cinturon"],
    "COLISION_DENT": ["diente", "abolladura", "dent"],
    "COLISION_SCRATCH": ["rayadura", "scratch", "arañazo"],
    "COLISION_CRAK": ["grieta", "crack", "quebrado"],
    "VIDRIOS_LUCES": ["vidrio", "cristal", "lampara", "luz rota", "glass", "lamp"],
    "OTROS": ["emergencia", "otro", "problema"],
}


def classify_text(texto: str) -> tuple[str, float]:
    t = (texto or "").lower()
    best, conf = "OTROS", 0.55
    for codigo, words in KEYWORDS.items():
        hits = sum(1 for w in words if w in t)
        if hits:
            c = min(0.95, 0.6 + 0.1 * hits)
            if c > conf:
                best, conf = codigo, c
    return best, conf


def fuse(text_res, img_res) -> tuple[str, float]:
    if img_res and text_res[0] == img_res[0]:
        return text_res[0], max(text_res[1], img_res[1])
    if img_res and img_res[1] > text_res[1]:
        return img_res
    return text_res


def priority_for(codigo: str, texto: str) -> str:
    base = {
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
    if any(
        w in (texto or "").lower()
        for w in ["emergencia", "peligro", "humo", "fuego", "herido"]
    ):
        return "ALTA"
    return base


def summarize(inc: dict, transcripcion: str) -> str:
    return (
        f"Incidente reportado en {inc.get('direccion') or 'ubicación GPS'}. "
        f"{transcripcion or inc.get('descripcion') or 'Sin descripción adicional.'}"
    )


async def run_ai_pipeline(incidente_id: str, tenant_id: str, delay_s: float = 0.0):
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    db = scoped_session(tenant_id)
    try:
        inc = db.execute(
            text("SELECT * FROM emergencias.incidente WHERE id = :id"),
            {"id": incidente_id},
        ).mappings().first()
        if not inc or inc["estado"] not in ("PENDIENTE", "BUSCANDO_TALLER"):
            return

        evs = db.execute(
            text("SELECT * FROM emergencias.evidencia WHERE incidente_id = :id"),
            {"id": incidente_id},
        ).mappings().all()

        transcripcion = ""
        for e in evs:
            if e["tipo"] == "AUDIO":
                transcripcion = (
                    e.get("transcripcion")
                    or e.get("contenido_texto")
                    or transcripcion
                )
            elif e["tipo"] == "TEXTO" and e.get("contenido_texto"):
                transcripcion = e["contenido_texto"]

        texto = (inc["descripcion"] or "") + " " + transcripcion
        text_res = classify_text(texto)
        img_res = None
        for e in evs:
            if e["tipo"] != "IMAGEN":
                continue
            url = e.get("url") or ""
            if models_available() and url and not url.startswith("sync/"):
                key = url.replace("local://", "")
                data = download_bytes(key)
                if data:
                    img_res = classify_image_bytes(data)
                    break
            if img_res is None and e.get("contenido_texto"):
                img_res = classify_text(e.get("contenido_texto") or texto)
                break

        codigo, conf = fuse(text_res, img_res) if img_res else text_res
        prio = priority_for(codigo, texto)
        tipo_id = TIPOS[codigo]
        resumen = summarize(dict(inc), transcripcion)

        db.execute(
            text(
                """INSERT INTO emergencias.clasificacion_ia
                (tenant_id, incidente_id, fuente, tipo_incidente_id, etiqueta, confianza,
                 prioridad_sugerida, modelo)
                VALUES (:t, :i, 'COMBINADA', :tp, :lbl, :c, :p, :m)"""
            ),
            {
                "t": tenant_id,
                "i": incidente_id,
                "tp": tipo_id,
                "lbl": codigo,
                "c": conf,
                "p": prio,
                "m": "yolov8-dashboard+cardd" if models_available() else "keywords",
            },
        )
        db.execute(
            text(
                """UPDATE emergencias.incidente
                SET tipo_incidente_id = :tp, prioridad = :p, resumen_ia = :r,
                    estado = 'BUSCANDO_TALLER'
                WHERE id = :i"""
            ),
            {"tp": tipo_id, "p": prio, "r": resumen, "i": incidente_id},
        )
        db.commit()
    finally:
        db.close()

    await assign_best_workshop(incidente_id, tenant_id)
