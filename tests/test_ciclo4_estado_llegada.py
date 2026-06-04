"""
Pruebas Ciclo 4: CU-36 actualizar estado y CU-37 llegada tecnico.

Run inside Docker:
  docker exec codigo_si2_p2-backend-1 pytest /app/tests/test_ciclo4_estado_llegada.py -v
"""

import uuid

import pytest
from sqlalchemy import text

TENANT_ID = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_ID = "44444444-0000-0000-0000-0000000000a2"
TALLER_CENTRO_ID = "66666666-0000-0000-0000-000000000001"
TALLER_SUR_ID = "66666666-0000-0000-0000-000000000002"
TECNICO_ID = "77777777-0000-0000-0000-000000000001"
VEHICULO_ID = "55555555-0000-0000-0000-000000000001"
TIPO_BATERIA = "33333333-0000-0000-0000-000000000001"


def login(client, email: str) -> tuple[dict, str]:
    r = client.post(
        "/auth/login",
        json={"email": email, "password": "password123", "tenant_id": TENANT_ID},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, token


def create_incident_with_assignment(db, inc_id: str, estado: str = "TALLER_ASIGNADO"):
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, tipo_incidente_id,
             estado, prioridad, descripcion, latitud, longitud, direccion, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, :tipo, :estado, 'MEDIA',
                    'Ciclo 4 test', -17.7833, -63.1821, 'Av. Canoto 100', 'SINCRONIZADO')"""
        ),
        {
            "id": inc_id,
            "t": TENANT_ID,
            "c": CONDUCTOR_ID,
            "v": VEHICULO_ID,
            "tipo": TIPO_BATERIA,
            "estado": estado,
        },
    )
    db.execute(
        text(
            """INSERT INTO emergencias.asignacion
            (tenant_id, incidente_id, taller_id, tecnico_id, estado, asignacion_automatica, respondido_at)
            VALUES (:t, :i, :tl, :tec, 'ACEPTADO', true, now())"""
        ),
        {"t": TENANT_ID, "i": inc_id, "tl": TALLER_CENTRO_ID, "tec": TECNICO_ID},
    )
    db.commit()


def cleanup(db, inc_id: str):
    db.rollback()
    db.execute(text("DELETE FROM emergencias.ubicacion_tracking WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.notificacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.asignacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.incidente_estado_historial WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.incidente WHERE id = :i"), {"i": inc_id})
    db.commit()


class TestCU36ActualizarEstado:
    def test_taller_actualiza_flujo_completo_de_estado(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        create_incident_with_assignment(db, inc_id)
        try:
            for estado in ["EN_CAMINO", "EN_ATENCION", "FINALIZADO"]:
                r = client.patch(
                    f"/incidentes/{inc_id}/estado",
                    headers=auth_taller,
                    json={"estado": estado, "comentario": f"Cambio a {estado}"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["estado"] == estado

            actual = db.execute(
                text("SELECT estado FROM emergencias.incidente WHERE id = :i"), {"i": inc_id}
            ).scalar()
            assert actual == "FINALIZADO"
        finally:
            cleanup(db, inc_id)

    def test_rechaza_transicion_invalida_desde_finalizado(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        create_incident_with_assignment(db, inc_id, estado="FINALIZADO")
        try:
            r = client.patch(
                f"/incidentes/{inc_id}/estado",
                headers=auth_taller,
                json={"estado": "EN_CAMINO", "comentario": "retroceso invalido"},
            )
            assert r.status_code == 409, r.text
        finally:
            cleanup(db, inc_id)

    def test_taller_no_asignado_no_puede_actualizar_estado(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_sur, _ = login(client, "sur@auxilionorte.com")
        create_incident_with_assignment(db, inc_id)
        try:
            r = client.patch(
                f"/incidentes/{inc_id}/estado",
                headers=auth_sur,
                json={"estado": "EN_CAMINO", "comentario": "no asignado"},
            )
            assert r.status_code in {403, 404}, r.text
        finally:
            cleanup(db, inc_id)


class TestCU37LlegadaTecnico:
    def test_marcar_llegada_emite_evento_y_notificacion(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        _, token = login(client, "carlos@mail.com")
        create_incident_with_assignment(db, inc_id, estado="EN_CAMINO")
        try:
            with client.websocket_connect(f"/ws/{TENANT_ID}/{inc_id}?token={token}") as ws:
                assert ws.receive_json()["type"] == "STATE_SNAPSHOT"
                r = client.patch(
                    f"/incidentes/{inc_id}/estado",
                    headers=auth_taller,
                    json={"estado": "EN_ATENCION", "comentario": "Tecnico llego"},
                )
                assert r.status_code == 200, r.text
                assert ws.receive_json()["type"] == "STATUS_CHANGED"
                assert ws.receive_json()["type"] == "TECH_ARRIVED"

            notif = db.execute(
                text(
                    """SELECT titulo, canal FROM emergencias.notificacion
                       WHERE incidente_id = :i AND usuario_id = :u
                       ORDER BY created_at DESC LIMIT 1"""
                ),
                {"i": inc_id, "u": CONDUCTOR_ID},
            ).first()
            assert notif is not None
            assert notif[0] == "Tecnico llego" or notif[0] == "Técnico llegó"
            assert notif[1] == "PUSH"
        finally:
            cleanup(db, inc_id)
