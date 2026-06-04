import uuid
from datetime import datetime, timezone

from sqlalchemy import text


TENANT_ID = "22222222-0000-0000-0000-000000000001"


def _login(client, email: str, tenant_id: str | None = None) -> dict[str, str]:
    body = {"email": email, "password": "password123"}
    if tenant_id:
        body["tenant_id"] = tenant_id
    r = client.post("/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_cu38_cu40_cu41_sync_idempotent_no_duplicates(client, login_conductor, db):
    headers = login_conductor()
    external_id = str(uuid.uuid4())
    vehicle_id = "55555555-0000-0000-0000-000000000001"
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "dispositivo": "pytest-ciclo5",
        "incidentes": [
            {
                "external_id": external_id,
                "vehiculo_id": vehicle_id,
                "descripcion": "Prueba offline ciclo 5",
                "latitud": -17.784,
                "longitud": -63.181,
                "direccion": "pytest sync",
                "client_created_at": now,
                "client_updated_at": now,
                "evidencias": [{"tipo": "TEXTO", "texto": "No arranca"}],
            }
        ],
    }

    before = db.execute(
        text("SELECT count(*) FROM emergencias.incidente WHERE external_id = :e"),
        {"e": external_id},
    ).scalar_one()

    r1 = client.post("/sync", json=body, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["results"][0]["status"] == "CREATED"

    r2 = client.post("/sync", json=body, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["results"][0]["status"] == "UPDATED"

    after = db.execute(
        text("SELECT count(*) FROM emergencias.incidente WHERE external_id = :e"),
        {"e": external_id},
    ).scalar_one()
    assert before == 0
    assert after == 1

    db.execute(text("DELETE FROM emergencias.sync_mapping WHERE external_id = :e"), {"e": external_id})
    db.execute(text("DELETE FROM emergencias.incidente WHERE external_id = :e"), {"e": external_id})
    db.commit()


def test_cu42_cu43_kpi_endpoints_tenant_scoped(client):
    adt_headers = _login(client, "ana@auxilionorte.com", TENANT_ID)
    for path in (
        "/kpis/resumen",
        "/kpis/por-tipo",
        "/kpis/talleres",
        "/kpis/zonas",
        "/kpis/sla",
        "/kpis/comisiones",
    ):
        r = client.get(path, headers=adt_headers)
        assert r.status_code == 200, (path, r.text)
        assert isinstance(r.json(), list)
        assert all(row["tenant_id"] == TENANT_ID for row in r.json())

    r = client.post("/kpis/refresh", headers=adt_headers)
    assert r.status_code == 200, r.text


def test_cu45_sla_patch(client, db):
    admin_headers = _login(client, "admin@plataforma.com")
    row = db.execute(
        text("SELECT id, tiempo_max_min FROM emergencias.sla_config LIMIT 1")
    ).mappings().first()
    assert row is not None
    original = row["tiempo_max_min"]
    patched = original + 1

    r = client.patch(f"/sla/{row['id']}", json={"tiempo_max_min": patched}, headers=admin_headers)
    assert r.status_code == 200, r.text

    updated = db.execute(
        text("SELECT tiempo_max_min FROM emergencias.sla_config WHERE id = :id"),
        {"id": row["id"]},
    ).scalar_one()
    assert updated == patched

    db.execute(
        text("UPDATE emergencias.sla_config SET tiempo_max_min = :tm WHERE id = :id"),
        {"tm": original, "id": row["id"]},
    )
    db.commit()


def test_cu46_cu47_cu48_tenant_admin_plan_endpoints(client, db):
    admin_headers = _login(client, "admin@plataforma.com")

    r = client.get("/tenants", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert any(t["nombre"] == "Auxilio Norte" for t in r.json()["items"])

    r = client.get("/tenants/planes", headers=admin_headers)
    assert r.status_code == 200, r.text
    plan_id = r.json()["items"][0]["id"]

    tenant_name = f"Pytest Tenant {uuid.uuid4()}"
    tenant_id = None
    r = client.post(
        "/tenants",
        json={"nombre": tenant_name, "dominio": None, "plan_id": plan_id},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]

    try:
        email = f"admin-{uuid.uuid4()}@pytest.com"
        r = client.post(
            f"/tenants/{tenant_id}/admin",
            json={"email": email, "nombre": "Pytest Admin"},
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text

        r = client.patch(f"/tenants/{tenant_id}/plan", json={"plan_id": plan_id}, headers=admin_headers)
        assert r.status_code == 200, r.text
    finally:
        if tenant_id:
            db.execute(text("DELETE FROM emergencias.usuario WHERE tenant_id = :t"), {"t": tenant_id})
            db.execute(text("DELETE FROM emergencias.tenant WHERE id = :t"), {"t": tenant_id})
            db.commit()
