"""
Tests for CU-22 to CU-26: Assignment Flow

Run inside Docker:
  docker exec codigo_si2_p2-backend-1 pytest /app/tests/ -v

Prerequisites:
  - DB seeded with 03_seed.sql (password: password123)
  - Seed data used:
      Tenant:        Auxilio Norte (22222222-...)
      Driver:        carlos@mail.com / password123
      Taller Centro: centro@auxilionorte.com / password123
      Taller Sur:    sur@auxilionorte.com / password123
      Vehicle:       ABC123 (55555555-0000-0000-0000-000000000001)
      Tecnico:       Luis Mecánico (77777777-0000-0000-0000-000000000001)
      Tipo BATERIA: 33333333-0000-0000-0000-000000000001
"""

import time
import uuid

import pytest
from sqlalchemy import text

TENANT_ID = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_ID = "44444444-0000-0000-0000-0000000000a2"
TALLER_CENTRO_USUARIO_ID = "44444444-0000-0000-0000-0000000000a4"
CONDUCTOR_MAIL = "carlos@mail.com"
TALLER_MAIL = "centro@auxilionorte.com"
VEHICULO_ID = "55555555-0000-0000-0000-000000000001"
TIPO_BATERIA = "33333333-0000-0000-0000-000000000001"
TECNICO_ID = "77777777-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Helpers (used via closure from conftest fixtures)
# ---------------------------------------------------------------------------

def create_incidente(db, inc_id: str, estado="BUSCANDO_TALLER", prioridad="MEDIA"):
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, tipo_incidente_id,
             estado, prioridad, latitud, longitud, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, :tp, :est, :pri,
                    -17.7833, -63.1821, 'SINCRONIZADO')"""
        ),
        {
            "id": inc_id, "t": TENANT_ID, "c": CONDUCTOR_ID, "v": VEHICULO_ID,
            "tp": TIPO_BATERIA, "est": estado, "pri": prioridad,
        },
    )
    db.commit()


def get_asignacion_id(db, inc_id: str) -> str | None:
    row = db.execute(
        text("SELECT id FROM emergencias.asignacion WHERE incidente_id = :id AND estado = 'ASIGNADO'"),
        {"id": inc_id},
    ).first()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# CU-22: Buscar Talleres Candidatos
# ---------------------------------------------------------------------------

class TestBuscarTalleresCandidatos:

    def test_devuelve_candidatos(self, client, db, cleanup_incidente, login_conductor):
        inc_id = str(uuid.uuid4())
        auth = login_conductor()
        create_incidente(db, inc_id)
        try:
            r = client.post(f"/incidentes/{inc_id}/buscar-talleres", headers=auth)
            assert r.status_code == 200, r.text
            candidatos = r.json()["candidatos"]
            assert len(candidatos) > 0
            assert "Taller Centro" in [c["nombre"] for c in candidatos]
        finally:
            cleanup_incidente(inc_id)

    def test_ordenado_por_distancia(self, client, db, cleanup_incidente, login_conductor):
        inc_id = str(uuid.uuid4())
        auth = login_conductor()
        create_incidente(db, inc_id)
        try:
            r = client.post(f"/incidentes/{inc_id}/buscar-talleres", headers=auth)
            assert r.status_code == 200
            distancias = [c["distancia_km"] for c in r.json()["candidatos"]]
            assert distancias == sorted(distancias), f"Not sorted: {distancias}"
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# CU-23: Asignar Taller Optimo
# ---------------------------------------------------------------------------

class TestAsignarTallerOptimo:

    def test_asignar_crea_asignacion_y_cambia_estado(
        self, client, db, cleanup_incidente, login_conductor
    ):
        inc_id = str(uuid.uuid4())
        auth = login_conductor()
        create_incidente(db, inc_id)
        try:
            r = client.post(f"/incidentes/{inc_id}/asignar", headers=auth)
            assert r.status_code == 200, r.text

            estado = db.execute(
                text("SELECT estado FROM emergencias.incidente WHERE id = :id"),
                {"id": inc_id},
            ).first()[0]
            assert estado == "TALLER_ASIGNADO", f"Got {estado}"

            asig = db.execute(
                text("""SELECT taller_id, estado, asignacion_automatica
                        FROM emergencias.asignacion
                        WHERE incidente_id = :id AND estado = 'ASIGNADO'"""),
                {"id": inc_id},
            ).first()
            assert asig is not None
            assert asig[2] is True

            taller_nombre = db.execute(
                text("SELECT nombre FROM emergencias.taller WHERE id = :id"),
                {"id": str(asig[0])},
            ).first()[0]
            assert taller_nombre == "Taller Centro"
        finally:
            cleanup_incidente(inc_id)

    def test_cualquier_usuario_autenticado_puede_asignar(self, client, db, cleanup_incidente, login_taller):
        inc_id = str(uuid.uuid4())
        auth_taller = login_taller()
        create_incidente(db, inc_id)
        try:
            r = client.post(f"/incidentes/{inc_id}/asignar", headers=auth_taller)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# CU-25: Aceptar Solicitud
# ---------------------------------------------------------------------------

class TestAceptarSolicitud:

    def test_aceptar_con_tecnico(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
        asig_id = get_asignacion_id(db, inc_id)
        assert asig_id is not None

        try:
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar",
                json={"tecnico_id": TECNICO_ID},
                headers=auth_t,
            )
            assert r.status_code == 200, r.text
            assert r.json()["estado"] == "EN_CAMINO"

            estado = db.execute(
                text("SELECT estado FROM emergencias.incidente WHERE id = :id"),
                {"id": inc_id},
            ).first()[0]
            assert estado == "EN_CAMINO"

            asig = db.execute(
                text("SELECT tecnico_id, estado, respondido_at FROM emergencias.asignacion WHERE id = :id"),
                {"id": asig_id},
            ).first()
            assert str(asig[0]) == TECNICO_ID
            assert asig[1] == "ACEPTADO"
            assert asig[2] is not None
        finally:
            cleanup_incidente(inc_id)

    def test_aceptar_sin_tecnico(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
        asig_id = get_asignacion_id(db, inc_id)
        try:
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar", json={}, headers=auth_t,
            )
            assert r.status_code == 200, r.text
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# CU-26: Rechazar Solicitud
# ---------------------------------------------------------------------------

class TestRechazarSolicitud:

    def test_rechazar_con_motivo(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
        asig_id = get_asignacion_id(db, inc_id)

        try:
            r = client.post(
                f"/asignaciones/{asig_id}/rechazar",
                json={"motivo": "Out of service hours"},
                headers=auth_t,
            )
            assert r.status_code == 200, r.text
            assert r.json()["estado"] == "BUSCANDO_TALLER"

            motivo = db.execute(
                text("SELECT motivo_rechazo FROM emergencias.asignacion WHERE id = :id"),
                {"id": asig_id},
            ).first()[0]
            assert motivo == "Out of service hours"
        finally:
            cleanup_incidente(inc_id)

    def test_rechazo_trigger_reasignacion(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
        asig_id = get_asignacion_id(db, inc_id)

        try:
            r = client.post(
                f"/asignaciones/{asig_id}/rechazar",
                json={"motivo": "Cannot attend"},
                headers=auth_t,
            )
            assert r.status_code == 200, r.text

            estado_rechazada = db.execute(
                text("SELECT estado FROM emergencias.asignacion WHERE id = :id"),
                {"id": asig_id},
            ).first()[0]
            assert estado_rechazada == "RECHAZADO"

            new_asigs = db.execute(
                text("""SELECT id FROM emergencias.asignacion
                         WHERE incidente_id = :id AND estado = 'ASIGNADO'"""),
                {"id": inc_id},
            ).fetchall()
            assert len(new_asigs) >= 1
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# CU-24: Notificar al Taller
# ---------------------------------------------------------------------------

class TestNotificarTaller:

    def test_notificacion_creada_al_asignar(
        self, client, db, cleanup_incidente, login_conductor
    ):
        inc_id = str(uuid.uuid4())
        auth = login_conductor()
        create_incidente(db, inc_id)
        try:
            client.post(f"/incidentes/{inc_id}/asignar", headers=auth)

            notif = db.execute(
                text("""SELECT titulo, canal FROM emergencias.notificacion
                         WHERE usuario_id = :uid AND incidente_id = :iid"""),
                {"uid": TALLER_CENTRO_USUARIO_ID, "iid": inc_id},
            ).first()
            assert notif is not None, f"No notification for taller user {TALLER_CENTRO_USUARIO_ID}"
            assert notif[0] == "Nueva solicitud de auxilio"
            assert notif[1] == "PUSH"
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# Taller endpoints
# ---------------------------------------------------------------------------

class TestTallerEndpoints:

    def test_listar_asignaciones(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)

        try:
            r = client.get("/talleres/asignaciones", headers=auth_t)
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert len(items) > 0
            found = next((a for a in items if a["incidente_id"] == inc_id), None)
            assert found is not None
            assert found["incidente_estado"] == "TALLER_ASIGNADO"
        finally:
            cleanup_incidente(inc_id)

    def test_filtrar_por_estado(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        inc_id = str(uuid.uuid4())
        auth_c = login_conductor()
        auth_t = login_taller()
        create_incidente(db, inc_id)
        client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)

        try:
            r = client.get(
                "/talleres/asignaciones",
                params={"estado": "ASIGNADO"},
                headers=auth_t,
            )
            assert r.status_code == 200
            for item in r.json()["items"]:
                assert item["estado"] == "ASIGNADO"
        finally:
            cleanup_incidente(inc_id)


# ---------------------------------------------------------------------------
# E2E: Full flow from report to acceptance
# ---------------------------------------------------------------------------

class TestFlujoCompletoE2E:

    def test_flujo_reportar_clasificar_asignar_aceptar(
        self, client, db, cleanup_incidente, login_conductor, login_taller
    ):
        auth_c = login_conductor()
        auth_t = login_taller()

        # 1. Driver reports
        r = client.post(
            "/incidentes",
            json={
                "vehiculo_id": VEHICULO_ID,
                "descripcion": "My car wont start, lights are weak, battery issue.",
                "latitud": -17.7833,
                "longitud": -63.1821,
                "direccion": "Av. Canoto 100, Santa Cruz",
            },
            headers=auth_c,
        )
        assert r.status_code == 201, f"Create failed: {r.text}"
        inc_id = r.json()["id"]

        # 2. Wait for AI pipeline (5s delay + processing)
        time.sleep(8)

        try:
            row = db.execute(
                text("SELECT estado, tipo_incidente_id, prioridad FROM emergencias.incidente WHERE id = :id"),
                {"id": inc_id},
            ).first()

            assert row is not None
            estado = row[0]
            assert estado in ("BUSCANDO_TALLER", "TALLER_ASIGNADO", "EN_CAMINO"), f"Got: {estado}"
            assert str(row[1]) == TIPO_BATERIA
            assert row[2] == "MEDIA"

            if estado == "TALLER_ASIGNADO":
                asig = db.execute(
                    text("""SELECT a.id, a.estado, t.nombre
                             FROM emergencias.asignacion a
                             JOIN emergencias.taller t ON t.id = a.taller_id
                             WHERE a.incidente_id = :iid"""),
                    {"iid": inc_id},
                ).first()
                assert asig is not None, "No assignment created"
                assert asig[1] == "ASIGNADO"
                assert asig[2] == "Taller Centro"

                # 3. Taller accepts
                r_acep = client.post(
                    f"/asignaciones/{asig[0]}/aceptar",
                    json={"tecnico_id": TECNICO_ID},
                    headers=auth_t,
                )
                assert r_acep.status_code == 200, r_acep.text

                estado_final = db.execute(
                    text("SELECT estado FROM emergencias.incidente WHERE id = :id"),
                    {"id": inc_id},
                ).first()[0]
                assert estado_final == "EN_CAMINO", f"Expected EN_CAMINO, got {estado_final}"

                tecnico = db.execute(
                    text("SELECT tecnico_id FROM emergencias.asignacion WHERE id = :id"),
                    {"id": asig[0]},
                ).first()[0]
                assert str(tecnico) == TECNICO_ID
        finally:
            cleanup_incidente(inc_id)
