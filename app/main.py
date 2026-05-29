from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    asignaciones,
    auth,
    cotizaciones,
    incidentes,
    kpi,
    pagos,
    sync,
    talleres,
    tecnicos,
    tenants,
    usuarios,
    vehiculos,
)
from .core.config import settings
from .ws import routes as ws_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Emergencias Vehiculares API",
    version="1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.get("/health")
def health():
    return {"status": "ok"}


for r in (
    auth,
    usuarios,
    vehiculos,
    talleres,
    tecnicos,
    incidentes,
    asignaciones,
    cotizaciones,
    pagos,
    kpi,
    tenants,
    sync,
):
    app.include_router(r.router)

app.include_router(ws_routes.router)
