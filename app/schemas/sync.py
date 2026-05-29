from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EvidenciaSync(BaseModel):
    tipo: str
    contenido_b64: str | None = None
    mime_type: str | None = None
    texto: str | None = None


class IncidenteSyncItem(BaseModel):
    external_id: UUID
    vehiculo_id: UUID
    descripcion: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    direccion: str | None = None
    client_created_at: datetime
    client_updated_at: datetime
    evidencias: list[EvidenciaSync] = []


class SyncBatch(BaseModel):
    dispositivo: str = "unknown"
    incidentes: list[IncidenteSyncItem]


class SyncResultItem(BaseModel):
    external_id: str
    incidente_id: str
    status: str
