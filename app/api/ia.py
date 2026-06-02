from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from sqlalchemy import text

from ..core.deps import CurrentUser, get_db, require_roles
from ..schemas.ia import (
    ClasificarImagenOut,
    ClasificarTextoOut,
    DeterminarPrioridadOut,
    GenerarResumenOut,
    TranscribirAudioOut,
)
from ..services.ai import classify_text, priority_for, summarize
from ..services.transcription import transcribe_audio_bytes
from ..services.vision import analyze_image_bytes

router = APIRouter(prefix="/ia", tags=["ia"])


@router.post("/clasificar-imagen", response_model=ClasificarImagenOut)
async def clasificar_imagen(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
):
    """CU-18: clasifica foto y sugiere descripción antes de reportar."""
    _ = user
    data = await file.read()
    if not data:
        raise HTTPException(400, "Imagen vacía")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Imagen demasiado grande (máx. 8 MB)")

    result = analyze_image_bytes(data)
    return ClasificarImagenOut(**result)


@router.post("/transcribir-audio", response_model=TranscribirAudioOut)
async def transcribir_audio(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
):
    """CU-17: convierte audio del conductor a texto."""
    _ = user
    data = await file.read()
    if not data:
        raise HTTPException(400, "Audio vacío")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Audio demasiado grande (máx. 5 MB)")

    mime = file.content_type or "audio/aac"
    try:
        text, motor = transcribe_audio_bytes(data, mime)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return TranscribirAudioOut(transcripcion=text, motor=motor)


@router.post("/clasificar-texto", response_model=ClasificarTextoOut)
async def clasificar_texto(
    texto: str,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
):
    """CU-19: clasifica el texto del conductor (descripción o transcripción) por keywords."""
    if not texto or len(texto.strip()) < 3:
        raise HTTPException(400, "Texto demasiado corto para clasificar")
    codigo, confianza = classify_text(texto)
    prio = priority_for(codigo, texto)
    return ClasificarTextoOut(codigo=codigo, confianza=confianza, prioridad_sugerida=prio)


@router.post("/generar-resumen/{incidente_id}", response_model=GenerarResumenOut)
async def generar_resumen(
    incidente_id: str = Path(..., description="UUID del incidente"),
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
    db=Depends(get_db),
):
    """CU-20: genera un resumen estructurado del incidente consultando la BD."""
    inc = db.execute(
        text(
            """SELECT i.descripcion, i.direccion, i.prioridad, i.tipo_incidente_id,
                      i.latitud, i.longitud
               FROM emergencias.incidente i
               WHERE i.id = :id"""
        ),
        {"id": incidente_id},
    ).mappings().first()

    if not inc:
        raise HTTPException(404, "Incidente no encontrado")

    transcripcion = None
    evs = db.execute(
        text(
            """SELECT transcripcion, contenido_texto
               FROM emergencias.evidencia
               WHERE incidente_id = :id AND tipo IN ('AUDIO', 'TEXTO')
               LIMIT 1"""
        ),
        {"id": incidente_id},
    ).mappings().all()
    for e in evs:
        transcripcion = e.get("transcripcion") or e.get("contenido_texto") or transcripcion

    resumen = summarize(
        {
            "direccion": inc.get("direccion"),
            "latitud": inc.get("latitud"),
            "longitud": inc.get("longitud"),
        },
        transcripcion,
    )
    tipo_nombre = inc.get("tipo_incidente_id")
    if inc.get("prioridad"):
        prioridad = inc["prioridad"]
    else:
        prioridad = priority_for(tipo_nombre or "OTROS", transcripcion or inc["descripcion"] or "")
    return GenerarResumenOut(resumen=resumen, codigo=tipo_nombre or "OTROS", prioridad_sugerida=prioridad)


_VALORES_CODIGO = {"BATERIA", "LLANTA", "MOTOR", "CHOQUE", "OTROS"}
_PALABRAS_EMERGENCIA = {"emergencia", "peligro", "humo", "fuego", "herido", "accidente"}


@router.post("/determinar-prioridad", response_model=DeterminarPrioridadOut)
async def determinar_prioridad(
    codigo: str,
    texto: str | None = None,
    user: CurrentUser = Depends(require_roles("CONDUCTOR")),
):
    """CU-21: determina la prioridad (ALTA/MEDIA/BAJA) y detecta si es emergencia."""
    if codigo not in _VALORES_CODIGO:
        raise HTTPException(400, f"Código inválido. Use uno de: {', '.join(sorted(_VALORES_CODIGO))}")
    prioridad = priority_for(codigo, texto or "")
    es_emergencia = any(p in (texto or "").lower() for p in _PALABRAS_EMERGENCIA)
    return DeterminarPrioridadOut(prioridad=prioridad, codigo=codigo, es_emergencia=es_emergencia)
