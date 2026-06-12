from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    asignaciones,
    auth,
    cotizaciones,
    ia,
    incidentes,
    kpi,
    pagos,
    plan_checkout,
    public,
    roles,
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
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.get("/health")
def health():
    return {"status": "ok"}


for r in (
    public,
    auth,
    ia,
    usuarios,
    vehiculos,
    talleres,
    tecnicos,
    incidentes,
    asignaciones,
    cotizaciones,
    pagos,
    plan_checkout,
    kpi,
    tenants,
    sync,
    roles,
):
    app.include_router(r.router)

app.include_router(ws_routes.router)
