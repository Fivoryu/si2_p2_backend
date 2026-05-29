import asyncio
from sqlalchemy import text

from ..core.db import scoped_session
from .assignment import assign_best_workshop

TIPOS = {
    "BATERIA": "33333333-0000-0000-0000-000000000001",
    "LLANTA": "33333333-0000-0000-0000-000000000002",
    "MOTOR": "33333333-0000-0000-0000-000000000003",
    "CHOQUE": "33333333-0000-0000-0000-000000000004",
    "OTROS": "33333333-0000-0000-0000-000000000005",
}

KEYWORDS = {
    "BATERIA": ["bateria", "no arranca", "descargada", "arranque"],
    "LLANTA": ["llanta", "pinchazo", "neumatico", "rueda"],
    "MOTOR": ["motor", "humo", "sobrecalent", "falla mecanica"],
    "CHOQUE": ["choque", "colision", "accidente", "golpe"],
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
        "CHOQUE": "ALTA",
        "MOTOR": "ALTA",
        "BATERIA": "MEDIA",
        "LLANTA": "MEDIA",
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


async def run_ai_pipeline(incidente_id: str, tenant_id: str):
    await asyncio.sleep(0.1)
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
            if e["tipo"] == "AUDIO" and e.get("transcripcion"):
                transcripcion = e["transcripcion"]
            elif e["tipo"] == "TEXTO" and e.get("contenido_texto"):
                transcripcion = e["contenido_texto"]

        texto = (inc["descripcion"] or "") + " " + transcripcion
        text_res = classify_text(texto)
        img_res = None
        for e in evs:
            if e["tipo"] == "IMAGEN":
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
                VALUES (:t, :i, 'COMBINADA', :tp, :lbl, :c, :p, 'keywords')"""
            ),
            {
                "t": tenant_id,
                "i": incidente_id,
                "tp": tipo_id,
                "lbl": codigo,
                "c": conf,
                "p": prio,
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
