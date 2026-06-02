"""Especialidades predeterminadas al crear taller."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.talleres import DEFAULT_ESPECIALIDADES

TENANT_ID = "22222222-0000-0000-0000-000000000001"
ADMIN_MAIL = "ana@auxilionorte.com"


@pytest.fixture
def admin_headers(client: TestClient) -> dict:
    r = client.post(
        "/auth/login",
        json={"email": ADMIN_MAIL, "password": "password123", "tenant_id": TENANT_ID},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_create_taller_seeds_default_especialidades(client: TestClient, admin_headers: dict):
    suffix = uuid.uuid4().hex[:8]
    email = f"taller-test-{suffix}@auxilionorte.com"
    r = client.post(
        "/talleres",
        headers=admin_headers,
        json={
            "nombre": f"Taller Test {suffix}",
            "email": email,
            "direccion": "Calle prueba",
        },
    )
    assert r.status_code == 201, r.text
    taller_id = r.json()["id"]

    r2 = client.get(f"/talleres/{taller_id}/especialidades", headers=admin_headers)
    assert r2.status_code == 200, r2.text
    nombres = sorted(item["nombre"] for item in r2.json()["items"])
    assert nombres == sorted(DEFAULT_ESPECIALIDADES)


def test_cannot_create_especialidad_manually(client: TestClient, admin_headers: dict):
    r = client.get("/talleres", headers=admin_headers)
    assert r.status_code == 200
    taller_id = r.json()["items"][0]["id"]

    r2 = client.post(
        f"/talleres/{taller_id}/especialidades",
        headers=admin_headers,
        json={"nombre": "Custom"},
    )
    assert r2.status_code == 403
