from pydantic import BaseModel, Field


class ClasificarImagenOut(BaseModel):
    codigo: str
    confianza: float = Field(ge=0, le=1)
    etiqueta: str | None = None
    fuente: str | None = None
    descripcion: str
    prioridad_sugerida: str
    modelo: str


class TranscribirAudioOut(BaseModel):
    transcripcion: str
    motor: str


class ClasificarTextoOut(BaseModel):
    codigo: str
    confianza: float = Field(ge=0, le=1)
    prioridad_sugerida: str
    modelo: str = "keywords"


class GenerarResumenOut(BaseModel):
    resumen: str
    codigo: str
    prioridad_sugerida: str


class DeterminarPrioridadOut(BaseModel):
    prioridad: str
    codigo: str
    es_emergencia: bool
