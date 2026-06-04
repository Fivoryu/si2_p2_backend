from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class IncidenteCreate(BaseModel):
    vehiculo_id: UUID
    descripcion: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    direccion: str | None = None
    external_id: UUID | None = None


class IncidenteOut(BaseModel):
    id: UUID
    estado: str
    prioridad: str
    tipo_incidente_id: UUID | None = None
    latitud: float | None = None
    longitud: float | None = None
    resumen_ia: str | None = None
    reportado_at: datetime
    vehiculo_id: UUID | None = None
    descripcion: str | None = None

    class Config:
        from_attributes = True


class EstadoPatch(BaseModel):
    estado: str
    comentario: str | None = None


class CancelarIn(BaseModel):
    motivo: str | None = None


class UbicacionIn(BaseModel):
    lat: float
    lng: float
    tecnico_id: str | None = None
    es_fake: bool = False


class SimularIn(BaseModel):
    velocidad_kmh: float = 40.0
    usar_fake: bool = True
    intervalo_seg: float = 3.0
