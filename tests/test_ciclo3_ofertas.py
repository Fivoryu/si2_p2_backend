"""
Pruebas completas del Ciclo 3:
- CU-22/CU-23: candidatos con precio/tiempo y asignacion.
- CU-25/CU-27/CU-28/CU-29: taller envia oferta editable y cliente elige.
- CU-30/CU-31/CU-32: pago, comision y factura.
- CU-49: calificacion post-atencion.

Run inside Docker:
  docker exec codigo_si2_p2-backend-1 pytest /app/tests/test_ciclo3_ofertas.py -v
"""

import uuid

import pytest
from sqlalchemy import text

from app.services.pricing import calculate_service_offer, difficulty_for

TENANT_ID = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_ID = "44444444-0000-0000-0000-0000000000a2"
VEHICULO_ID = "55555555-0000-0000-0000-000000000001"
TIPO_BATERIA = "33333333-0000-0000-0000-000000000001"
TALLER_CENTRO_ID = "66666666-0000-0000-0000-000000000001"
TECNICO_ID = "77777777-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def ensure_ciclo3_schema(db):
    """Keep tests runnable even if the DB was created before migration 08."""
    db.execute(
        text(
            """ALTER TABLE emergencias.cotizacion
               ADD COLUMN IF NOT EXISTS asignacion_id UUID REFERENCES emergencias.asignacion(id) ON DELETE SET NULL,
               ADD COLUMN IF NOT EXISTS precio_sugerido NUMERIC(10,2),
               ADD COLUMN IF NOT EXISTS tiempo_estimado_min INTEGER,
               ADD COLUMN IF NOT EXISTS tiempo_llegada_min INTEGER,
               ADD COLUMN IF NOT EXISTS dificultad VARCHAR(20),
               ADD COLUMN IF NOT EXISTS comentario_taller TEXT"""
        )
    )
    db.execute(
        text(
            """ALTER TABLE emergencias.taller_candidato
               ADD COLUMN IF NOT EXISTS precio_sugerido NUMERIC(10,2),
               ADD COLUMN IF NOT EXISTS dificultad VARCHAR(20)"""
        )
    )
    db.execute(
        text(
            """CREATE TABLE IF NOT EXISTS emergencias.calificacion_servicio (
               id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
               tenant_id UUID NOT NULL REFERENCES emergencias.tenant(id) ON DELETE CASCADE,
               incidente_id UUID NOT NULL REFERENCES emergencias.incidente(id) ON DELETE CASCADE,
               taller_id UUID NOT NULL REFERENCES emergencias.taller(id) ON DELETE CASCADE,
               conductor_id UUID NOT NULL REFERENCES emergencias.usuario(id) ON DELETE CASCADE,
               estrellas INTEGER NOT NULL CHECK (estrellas BETWEEN 1 AND 5),
               comentario TEXT,
               created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
               CONSTRAINT uq_calificacion_incidente UNIQUE (incidente_id)
            )"""
        )
    )
    db.execute(
        text(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_cotizacion_asignacion
               ON emergencias.cotizacion(asignacion_id)
               WHERE asignacion_id IS NOT NULL"""
        )
    )
    db.commit()


def login(client, email: str) -> dict:
    r = client.post(
        "/auth/login",
        json={"email": email, "password": "password123", "tenant_id": TENANT_ID},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def create_incidente(db, inc_id: str, prioridad: str = "MEDIA"):
    db.execute(
        text(
            """INSERT INTO emergencias.incidente
            (id, tenant_id, conductor_id, vehiculo_id, tipo_incidente_id,
             estado, prioridad, descripcion, latitud, longitud, estado_sincronizacion)
            VALUES (:id, :t, :c, :v, :tipo, 'BUSCANDO_TALLER', :pri,
                    'Bateria descargada', -17.7833, -63.1821, 'SINCRONIZADO')"""
        ),
        {
            "id": inc_id,
            "t": TENANT_ID,
            "c": CONDUCTOR_ID,
            "v": VEHICULO_ID,
            "tipo": TIPO_BATERIA,
            "pri": prioridad,
        },
    )
    db.commit()


def cleanup_full(db, inc_id: str):
    db.execute(text("DELETE FROM emergencias.calificacion_servicio WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(
        text(
            """DELETE FROM emergencias.factura
               WHERE pago_id IN (SELECT id FROM emergencias.pago WHERE incidente_id = :i)"""
        ),
        {"i": inc_id},
    )
    db.execute(text("DELETE FROM emergencias.pago WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.cotizacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.asignacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.taller_candidato WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.notificacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.incidente WHERE id = :i"), {"i": inc_id})
    db.commit()


def assigned_id(db, inc_id: str) -> str:
    row = db.execute(
        text("SELECT id FROM emergencias.asignacion WHERE incidente_id = :i AND estado = 'ASIGNADO'"),
        {"i": inc_id},
    ).first()
    assert row is not None
    return str(row[0])


class TestAlgoritmosCiclo3:
    def test_dificultad_por_tipo_y_prioridad(self):
        assert difficulty_for("MOTOR", "MEDIA") == "ALTA"
        assert difficulty_for("BATERIA_CARGADOR", "MEDIA") == "MEDIA"
        assert difficulty_for("OTROS", "BAJA") == "BAJA"
        assert difficulty_for("LLANTA", "ALTA") == "ALTA"

    def test_precio_y_tiempo_suben_con_distancia_y_dificultad(self):
        corto = calculate_service_offer("BATERIA_CARGADOR", "MEDIA", 1.0, 4.5, 0)
        lejos = calculate_service_offer("BATERIA_CARGADOR", "MEDIA", 20.0, 4.5, 0)
        dificil = calculate_service_offer("MOTOR", "ALTA", 1.0, 4.5, 0)

        assert corto.precio_sugerido > 0
        assert corto.tiempo_total_min > 0
        assert lejos.precio_sugerido > corto.precio_sugerido
        assert lejos.tiempo_llegada_min > corto.tiempo_llegada_min
        assert dificil.precio_sugerido > corto.precio_sugerido
        assert dificil.tiempo_reparacion_min > corto.tiempo_reparacion_min

    def test_calidad_de_servicio_afecta_precio(self):
        baja = calculate_service_offer("BATERIA_CARGADOR", "MEDIA", 2.0, 2.0, 0)
        alta = calculate_service_offer("BATERIA_CARGADOR", "MEDIA", 2.0, 5.0, 0)
        assert alta.precio_sugerido > baja.precio_sugerido


class TestFlujoCompletoCiclo3:
    def test_flujo_oferta_seleccion_pago_factura_calificacion(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_c = login(client, "carlos@mail.com")
        auth_t = login(client, "centro@auxilionorte.com")
        create_incidente(db, inc_id)
        try:
            r = client.post(f"/incidentes/{inc_id}/buscar-talleres", headers=auth_c)
            assert r.status_code == 200, r.text
            candidatos = r.json()["candidatos"]
            assert candidatos
            first = candidatos[0]
            assert first["precio_sugerido"] > 0
            assert first["tiempo_total_min"] > 0
            assert first["dificultad"] in {"BAJA", "MEDIA", "ALTA"}

            r = client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
            assert r.status_code == 200, r.text
            asig_id = assigned_id(db, inc_id)

            r = client.get("/talleres/asignaciones", headers=auth_t)
            assert r.status_code == 200, r.text
            item = next(a for a in r.json()["items"] if a["id"] == asig_id)
            assert item["precio_sugerido"] is not None
            assert item["tiempo_llegada_min"] is not None

            precio_sugerido = float(item["precio_sugerido"])
            precio_ofertado = round(precio_sugerido - 10, 2)
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar-con-oferta",
                headers=auth_t,
                json={
                    "precio_ofertado": precio_ofertado,
                    "tiempo_estimado_min": 40,
                    "tecnico_id": TECNICO_ID,
                    "comentario": "Oferta rapida con repuesto disponible.",
                },
            )
            assert r.status_code == 201, r.text
            oferta_id = r.json()["cotizacion_id"]
            assert r.json()["precio_ofertado"] == precio_ofertado

            r = client.get(f"/incidentes/{inc_id}/ofertas", headers=auth_c)
            assert r.status_code == 200, r.text
            ofertas = r.json()["items"]
            assert len(ofertas) == 1
            assert ofertas[0]["id"] == oferta_id
            assert float(ofertas[0]["monto"]) == precio_ofertado
            assert ofertas[0]["estado"] == "PENDIENTE"

            r = client.post(f"/cotizaciones/{oferta_id}/seleccionar", headers=auth_c)
            assert r.status_code == 200, r.text
            assert r.json()["estado"] == "ACEPTADA"

            state = db.execute(
                text(
                    """SELECT i.estado, a.estado, c.estado
                       FROM emergencias.incidente i
                       JOIN emergencias.asignacion a ON a.incidente_id = i.id
                       JOIN emergencias.cotizacion c ON c.asignacion_id = a.id
                       WHERE i.id = :i"""
                ),
                {"i": inc_id},
            ).first()
            assert state == ("EN_CAMINO", "ACEPTADO", "ACEPTADA")

            r = client.post(
                "/pagos/mock-complete",
                headers=auth_c,
                json={"incidente_id": inc_id, "cotizacion_id": oferta_id},
            )
            assert r.status_code == 200, r.text
            assert r.json()["estado"] == "COMPLETADO"
            assert r.json()["factura"].startswith("FAC-")

            pago = db.execute(
                text(
                    """SELECT p.monto, p.comision_plataforma, p.monto_taller, f.numero
                       FROM emergencias.pago p
                       JOIN emergencias.factura f ON f.pago_id = p.id
                       WHERE p.incidente_id = :i"""
                ),
                {"i": inc_id},
            ).first()
            assert pago is not None
            assert float(pago[1]) == round(float(pago[0]) * 0.10, 2)
            assert float(pago[2]) == round(float(pago[0]) - float(pago[1]), 2)

            db.execute(text("UPDATE emergencias.incidente SET estado = 'FINALIZADO' WHERE id = :i"), {"i": inc_id})
            db.commit()
            r = client.post(
                f"/incidentes/{inc_id}/calificacion",
                headers=auth_c,
                json={"estrellas": 5, "comentario": "Excelente servicio"},
            )
            assert r.status_code == 201, r.text
            rating = db.execute(
                text("SELECT calificacion FROM emergencias.taller WHERE id = :t"),
                {"t": TALLER_CENTRO_ID},
            ).scalar()
            assert float(rating) == 5.0
        finally:
            cleanup_full(db, inc_id)


class TestCasosNegativosCiclo3:
    def test_precio_cero_no_es_aceptado(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_c = login(client, "carlos@mail.com")
        auth_t = login(client, "centro@auxilionorte.com")
        create_incidente(db, inc_id)
        try:
            client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
            asig_id = assigned_id(db, inc_id)
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar-con-oferta",
                headers=auth_t,
                json={"precio_ofertado": 0, "tiempo_estimado_min": 20},
            )
            assert r.status_code == 422, r.text
        finally:
            cleanup_full(db, inc_id)

    def test_no_se_puede_pagar_cotizacion_no_seleccionada(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_c = login(client, "carlos@mail.com")
        auth_t = login(client, "centro@auxilionorte.com")
        create_incidente(db, inc_id)
        try:
            client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
            asig_id = assigned_id(db, inc_id)
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar-con-oferta",
                headers=auth_t,
                json={"precio_ofertado": 120, "tiempo_estimado_min": 20},
            )
            assert r.status_code == 201, r.text
            cot_id = r.json()["cotizacion_id"]

            r = client.post(
                "/pagos/mock-complete",
                headers=auth_c,
                json={"incidente_id": inc_id, "cotizacion_id": cot_id},
            )
            assert r.status_code == 409, r.text
        finally:
            cleanup_full(db, inc_id)

    def test_no_se_puede_calificar_antes_de_finalizar(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_c = login(client, "carlos@mail.com")
        auth_t = login(client, "centro@auxilionorte.com")
        create_incidente(db, inc_id)
        try:
            client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
            asig_id = assigned_id(db, inc_id)
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar-con-oferta",
                headers=auth_t,
                json={"precio_ofertado": 120, "tiempo_estimado_min": 20},
            )
            cot_id = r.json()["cotizacion_id"]
            client.post(f"/cotizaciones/{cot_id}/seleccionar", headers=auth_c)

            r = client.post(
                f"/incidentes/{inc_id}/calificacion",
                headers=auth_c,
                json={"estrellas": 5},
            )
            assert r.status_code == 409, r.text
        finally:
            cleanup_full(db, inc_id)

    def test_no_se_puede_calificar_dos_veces(self, client, db):
        inc_id = str(uuid.uuid4())
        auth_c = login(client, "carlos@mail.com")
        auth_t = login(client, "centro@auxilionorte.com")
        create_incidente(db, inc_id)
        try:
            client.post(f"/incidentes/{inc_id}/asignar", headers=auth_c)
            asig_id = assigned_id(db, inc_id)
            r = client.post(
                f"/asignaciones/{asig_id}/aceptar-con-oferta",
                headers=auth_t,
                json={"precio_ofertado": 120, "tiempo_estimado_min": 20},
            )
            cot_id = r.json()["cotizacion_id"]
            client.post(f"/cotizaciones/{cot_id}/seleccionar", headers=auth_c)
            db.execute(text("UPDATE emergencias.incidente SET estado = 'FINALIZADO' WHERE id = :i"), {"i": inc_id})
            db.commit()

            first = client.post(
                f"/incidentes/{inc_id}/calificacion",
                headers=auth_c,
                json={"estrellas": 4},
            )
            assert first.status_code == 201, first.text
            second = client.post(
                f"/incidentes/{inc_id}/calificacion",
                headers=auth_c,
                json={"estrellas": 5},
            )
            assert second.status_code == 409, second.text
        finally:
            cleanup_full(db, inc_id)
