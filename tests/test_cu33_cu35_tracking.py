"""
Pruebas PLAN CU-33, CU-34, CU-35 + animación técnico.

Run inside Docker:
  docker exec codigo_si2_p2-backend-1 pytest /app/tests/test_cu33_cu35_tracking.py -v
"""

import uuid

import pytest
from sqlalchemy import text

TENANT_ID = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_ID = "44444444-0000-0000-0000-0000000000a2"
TALLER_ID = "66666666-0000-0000-0000-000000000001"
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


def create_active_incident(db, inc_id: str, estado: str = "EN_CAMINO"):
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, tipo_incidente_id,
             estado, prioridad, descripcion, latitud, longitud, direccion, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, :tipo, :estado, 'MEDIA',
                    'Tracking test', -17.7833, -63.1821, 'Av. Canoto 100', 'SINCRONIZADO')"""
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
        {"t": TENANT_ID, "i": inc_id, "tl": TALLER_ID, "tec": TECNICO_ID},
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


class TestCU33WebSocket:
    def test_ws_snapshot_ping_y_rechaza_token_invalido(self, client, db):
        inc_id = str(uuid.uuid4())
        _, token = login(client, "carlos@mail.com")
        create_active_incident(db, inc_id)
        try:
            with client.websocket_connect(f"/ws/{TENANT_ID}/{inc_id}?token={token}") as ws:
                snap = ws.receive_json()
                assert snap["type"] == "STATE_SNAPSHOT"
                assert snap["incident_id"] == inc_id
                assert snap["data"]["estado"] == "EN_CAMINO"
                assert snap["data"]["latitud"] is not None

                ws.send_json({"type": "PING"})
                assert ws.receive_json()["type"] == "PONG"

            with pytest.raises(Exception):
                with client.websocket_connect(f"/ws/{TENANT_ID}/{inc_id}?token=bad-token"):
                    pass
        finally:
            cleanup(db, inc_id)


class TestCU34TrackingTecnico:
    def test_post_ubicacion_persiste_y_emite_tech_location(self, client, db):
        inc_id = str(uuid.uuid4())
        _, token = login(client, "carlos@mail.com")
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        create_active_incident(db, inc_id)
        try:
            with client.websocket_connect(f"/ws/{TENANT_ID}/{inc_id}?token={token}") as ws:
                assert ws.receive_json()["type"] == "STATE_SNAPSHOT"

                r = client.post(
                    f"/incidentes/{inc_id}/ubicacion",
                    headers=auth_taller,
                    json={
                        "lat": -17.7901,
                        "lng": -63.1802,
                        "tecnico_id": TECNICO_ID,
                        "es_fake": True,
                    },
                )
                assert r.status_code == 200, r.text
                assert r.json()["ok"] is True

                evt = ws.receive_json()
                assert evt["type"] == "TECH_LOCATION"
                assert evt["incident_id"] == inc_id
                assert evt["data"]["lat"] == -17.7901
                assert evt["data"]["lng"] == -63.1802
                assert evt["data"]["tecnico_id"] == TECNICO_ID

            row = db.execute(
                text(
                    """SELECT latitud, longitud, tecnico_id, es_fake
                       FROM emergencias.ubicacion_tracking
                       WHERE incidente_id = :i
                       ORDER BY created_at DESC LIMIT 1"""
                ),
                {"i": inc_id},
            ).first()
            assert row is not None
            assert float(row[0]) == -17.7901
            assert float(row[1]) == -63.1802
            assert str(row[2]) == TECNICO_ID
            assert row[3] is True
        finally:
            cleanup(db, inc_id)

    def test_simular_ruta_genera_puntos_para_animacion(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        create_active_incident(db, inc_id, estado="EN_CAMINO")
        try:
            r = client.post(
                f"/incidentes/{inc_id}/simular",
                headers=auth_taller,
                json={"velocidad_kmh": 60, "usar_fake": True, "intervalo_seg": 0.001},
            )
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
            assert r.json()["puntos"] > 1

            # La simulación corre como task async; con intervalo mínimo debe guardar puntos pronto.
            import time

            time.sleep(0.2)
            count = db.execute(
                text("SELECT count(*) FROM emergencias.ubicacion_tracking WHERE incidente_id = :i"),
                {"i": inc_id},
            ).scalar()
            assert count >= 1
        finally:
            cleanup(db, inc_id)


class TestCU35NotificacionesEstado:
    def test_cambio_estado_emite_ws_y_guarda_notificacion_cliente(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_taller, _ = login(client, "centro@auxilionorte.com")
        _, token = login(client, "carlos@mail.com")
        create_active_incident(db, inc_id)
        try:
            with client.websocket_connect(f"/ws/{TENANT_ID}/{inc_id}?token={token}") as ws:
                assert ws.receive_json()["type"] == "STATE_SNAPSHOT"
                r = client.patch(
                    f"/incidentes/{inc_id}/estado",
                    headers=auth_taller,
                    json={"estado": "EN_ATENCION", "comentario": "Técnico llegó"},
                )
                assert r.status_code == 200, r.text

                status_evt = ws.receive_json()
                assert status_evt["type"] == "STATUS_CHANGED"
                assert status_evt["data"]["estado_anterior"] == "EN_CAMINO"
                assert status_evt["data"]["estado_nuevo"] == "EN_ATENCION"

                arrived_evt = ws.receive_json()
                assert arrived_evt["type"] == "TECH_ARRIVED"

            notif = db.execute(
                text(
                    """SELECT titulo, mensaje, canal
                       FROM emergencias.notificacion
                       WHERE incidente_id = :i AND usuario_id = :u
                       ORDER BY created_at DESC LIMIT 1"""
                ),
                {"i": inc_id, "u": CONDUCTOR_ID},
            ).first()
            assert notif is not None
            assert notif[0] == "Técnico llegó"
            assert notif[2] == "PUSH"

            estado = db.execute(
                text("SELECT estado FROM emergencias.incidente WHERE id = :i"),
                {"i": inc_id},
            ).scalar()
            assert estado == "EN_ATENCION"
        finally:
            cleanup(db, inc_id)
