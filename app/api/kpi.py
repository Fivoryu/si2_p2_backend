from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..core.deps import CurrentUser, get_current_user, get_db, require_roles

router = APIRouter(tags=["kpi"])


def _tenant_filter(user: CurrentUser, tenant_id: str | None) -> str | None:
    if user.is_platform_admin:
        return tenant_id
    return user.tenant


@router.get("/kpis/resumen")
def kpis_resumen(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_resumen_tenant
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/por-tipo")
def kpis_por_tipo(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_incidentes_por_tipo
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/talleres")
def kpis_talleres(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_talleres_eficientes
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/zonas")
def kpis_zonas(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_zonas
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/sla")
def kpis_sla(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_sla
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kpis/comisiones")
def kpis_comisiones(
    tenant_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = _tenant_filter(user, tenant_id)
    rows = db.execute(
        text(
            """SELECT * FROM emergencias.mv_kpi_comisiones
            WHERE (:tid IS NULL OR tenant_id = CAST(:tid AS uuid))"""
        ),
        {"tid": tid},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/kpis/refresh")
def refresh_kpis(
    user: CurrentUser = Depends(require_roles("ADMIN_TENANT", "ADMIN_PLATAFORMA")),
    db=Depends(get_db),
):
    db.execute(text("SELECT emergencias.refrescar_kpis()"))
    return {"ok": True}


class SlaIn(BaseModel):
    tipo_incidente_id: str
    tiempo_max_min: int


@router.get("/sla")
def list_sla(
    user: CurrentUser = Depends(require_roles("ADMIN_PLATAFORMA", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    rows = db.execute(text("SELECT * FROM emergencias.sla_config")).mappings().all()
    return [dict(r) for r in rows]


@router.post("/sla", status_code=201)
def create_sla(
    body: SlaIn,
    user: CurrentUser = Depends(require_roles("ADMIN_PLATAFORMA", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    import uuid

    sid = str(uuid.uuid4())
    db.execute(
        text(
            """INSERT INTO emergencias.sla_config
            (id, tenant_id, tipo_incidente_id, tiempo_max_min)
            VALUES (:id, :t, :tp, :tm)"""
        ),
        {
            "id": sid,
            "t": user.tenant,
            "tp": body.tipo_incidente_id,
            "tm": body.tiempo_max_min,
        },
    )
    return {"id": sid}


class SlaPatch(BaseModel):
    tiempo_max_min: int


@router.patch("/sla/{sla_id}")
def patch_sla(
    sla_id: str,
    body: SlaPatch,
    user: CurrentUser = Depends(require_roles("ADMIN_PLATAFORMA", "ADMIN_TENANT")),
    db=Depends(get_db),
):
    db.execute(
        text(
            """UPDATE emergencias.sla_config
            SET tiempo_max_min = :tm, updated_at = now()
            WHERE id = :id"""
        ),
        {"tm": body.tiempo_max_min, "id": sla_id},
    )
    return {"ok": True}
