"""
Pytest configuration and shared fixtures for backend tests.

Architecture:
  - Tests run inside the backend container via `docker compose exec`
  - TestClient mounts the FastAPI app in-process for DB consistency
  - Seed data (03_seed.sql) must be loaded before running tests

Usage:
  docker exec codigo_si2_p2-backend-1 pytest /app/tests/ -v
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Seed constants (should match 03_seed.sql)
# ---------------------------------------------------------------------------

TENANT_ID = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_MAIL = "carlos@mail.com"
TALLER_MAIL = "centro@auxilionorte.com"


# ---------------------------------------------------------------------------
# Session-scoped TestClient
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client() -> TestClient:
    """FastAPI TestClient using the in-process app."""
    from app.main import app as _app

    with TestClient(_app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Auth helpers (exposed as fixtures for convenience)
# ---------------------------------------------------------------------------

@pytest.fixture
def login_conductor(client: TestClient) -> Callable[[], dict]:
    """Login as carlos@mail.com and return auth headers."""
    def _auth():
        r = client.post("/auth/login", json={
            "email": CONDUCTOR_MAIL,
            "password": "password123",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 200, f"Conductor login failed: {r.text}"
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _auth


@pytest.fixture
def login_taller(client: TestClient) -> Callable[[], dict]:
    """Login as centro@auxilionorte.com and return auth headers."""
    def _auth():
        r = client.post("/auth/login", json={
            "email": TALLER_MAIL,
            "password": "password123",
            "tenant_id": TENANT_ID,
        })
        assert r.status_code == 200, f"Taller login failed: {r.text}"
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _auth


@pytest.fixture
def db():
    """Direct SQLAlchemy session to the real database."""
    from sqlalchemy import text
    from app.core.db import SessionLocal

    db = SessionLocal()
    db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": TENANT_ID})
    yield db
    db.rollback()
    db.close()


@pytest.fixture
def cleanup_incidente(db):
    """Cleanup helper. Returns a function that removes all test data for an incident."""
    from sqlalchemy import text

    def _cleanup(inc_id: str):
        db.execute(text("DELETE FROM emergencias.asignacion WHERE incidente_id = :i"), {"i": inc_id})
        db.execute(text("DELETE FROM emergencias.taller_candidato WHERE incidente_id = :i"), {"i": inc_id})
        db.execute(text("DELETE FROM emergencias.notificacion WHERE incidente_id = :i"), {"i": inc_id})
        db.execute(text("DELETE FROM emergencias.incidente WHERE id = :i"), {"i": inc_id})
        db.commit()

    return _cleanup
