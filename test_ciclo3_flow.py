import json
import urllib.request
import uuid

from sqlalchemy import text

from app.core.db import SessionLocal


BASE = "http://localhost:8000"
TENANT = "22222222-0000-0000-0000-000000000001"
CONDUCTOR_ID = "44444444-0000-0000-0000-0000000000a2"
VEHICULO_ID = "55555555-0000-0000-0000-000000000001"
TIPO_BATERIA = "33333333-0000-0000-0000-000000000001"
TECNICO_ID = "77777777-0000-0000-0000-000000000001"


def post(path, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req).read().decode())


def get(path, token):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read().decode())


def login(email):
    return post("/auth/login", payload={"email": email, "password": "password123", "tenant_id": TENANT})["access_token"]


inc_id = str(uuid.uuid4())
db = SessionLocal()
db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": TENANT})
db.execute(
    text(
        """INSERT INTO emergencias.incidente
        (id, tenant_id, conductor_id, vehiculo_id, tipo_incidente_id, estado,
         prioridad, descripcion, latitud, longitud, estado_sincronizacion)
        VALUES (:id, :t, :c, :v, :tipo, 'BUSCANDO_TALLER', 'MEDIA',
                'Bateria descargada', -17.7833, -63.1821, 'SINCRONIZADO')"""
    ),
    {"id": inc_id, "t": TENANT, "c": CONDUCTOR_ID, "v": VEHICULO_ID, "tipo": TIPO_BATERIA},
)
db.commit()

try:
    conductor = login("carlos@mail.com")
    taller = login("centro@auxilionorte.com")
    print("INCIDENTE", inc_id)

    candidatos = post(f"/incidentes/{inc_id}/buscar-talleres", conductor)
    print("CANDIDATOS", len(candidatos["candidatos"]))
    print("PRECIO_SUGERIDO", candidatos["candidatos"][0]["precio_sugerido"])

    post(f"/incidentes/{inc_id}/asignar", conductor)
    asignaciones = get("/talleres/asignaciones", taller)["items"]
    asig = next(a for a in asignaciones if a["incidente_id"] == inc_id)
    print("ASIGNACION", asig["id"], asig["precio_sugerido"])

    oferta = post(
        f"/asignaciones/{asig['id']}/aceptar-con-oferta",
        taller,
        {
            "precio_ofertado": float(asig["precio_sugerido"]) - 10,
            "tiempo_estimado_min": 40,
            "tecnico_id": TECNICO_ID,
            "comentario": "Puedo llegar rapido y con repuesto.",
        },
    )
    print("OFERTA", oferta)

    ofertas = get(f"/incidentes/{inc_id}/ofertas", conductor)["items"]
    print("OFERTAS", len(ofertas), ofertas[0]["monto"])

    post(f"/cotizaciones/{ofertas[0]['id']}/seleccionar", conductor)
    pago = post("/pagos/mock-complete", conductor, {"incidente_id": inc_id, "cotizacion_id": ofertas[0]["id"]})
    print("PAGO", pago)

    # Simula servicio finalizado para habilitar calificacion post-atencion.
    db.execute(text("UPDATE emergencias.incidente SET estado = 'FINALIZADO' WHERE id = :i"), {"i": inc_id})
    db.commit()
    cal = post(f"/incidentes/{inc_id}/calificacion", conductor, {"estrellas": 5, "comentario": "Buen servicio"})
    print("CALIFICACION", cal)
finally:
    db.execute(text("DELETE FROM emergencias.calificacion_servicio WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.factura WHERE pago_id IN (SELECT id FROM emergencias.pago WHERE incidente_id = :i)"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.pago WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.cotizacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.asignacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.taller_candidato WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.notificacion WHERE incidente_id = :i"), {"i": inc_id})
    db.execute(text("DELETE FROM emergencias.incidente WHERE id = :i"), {"i": inc_id})
    db.commit()
    db.close()
