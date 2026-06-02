import json
import time
import urllib.request

TENANT = "22222222-0000-0000-0000-000000000001"
BASE = "http://localhost:8000"
def login(email: str, tenant_id: str) -> dict:
    data = json.dumps(
        {"email": email, "password": "password123", "tenant_id": tenant_id}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read().decode())


def post_json(url: str, headers: dict, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    return json.loads(urllib.request.urlopen(req).read().decode())


def get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    return json.loads(urllib.request.urlopen(req).read().decode())


def wait_for_classification(incidente_id: str, headers: dict, timeout_s: float = 12.0) -> dict:
    """Espera al pipeline de IA (delay ~5s) que clasifica y puede asignar taller."""
    deadline = time.time() + timeout_s
    last_inc = {}
    while time.time() < deadline:
        payload = get_json(f"{BASE}/incidentes/{incidente_id}", headers)
        last_inc = payload.get("incidente", payload)
        if last_inc.get("tipo_incidente_id"):
            return last_inc
        time.sleep(1)
    return last_inc


conductor = login("carlos@mail.com", TENANT)
taller = login("centro@auxilionorte.com", TENANT)

hconductor = {
    "Authorization": f"Bearer {conductor['access_token']}",
    "Content-Type": "application/json",
}
htaller = {
    "Authorization": f"Bearer {taller['access_token']}",
    "Content-Type": "application/json",
}

print("=== FLUJO CLIENTE ===")
inc = post_json(
    f"{BASE}/incidentes",
    hconductor,
    {
        "vehiculo_id": "55555555-0000-0000-0000-000000000001",
        "descripcion": "Batería descargada, el auto no arranca",
        "latitud": -17.7833,
        "longitud": -63.1821,
    },
)
INC_ID = inc["id"]
print(f"Incidente creado: {INC_ID}")

print("Esperando clasificación IA…")
inc = wait_for_classification(INC_ID, hconductor)
print(
    f"Estado: {inc.get('estado')} | tipo: {inc.get('tipo_incidente_id')} | "
    f"prioridad: {inc.get('prioridad')}"
)

cands = post_json(f"{BASE}/incidentes/{INC_ID}/buscar-talleres", hconductor)
candidatos = cands.get("candidatos", [])
print(f"Candidatos: {len(candidatos)}")
for c in candidatos:
    print(f"  {c.get('nombre', '?')} - {round(c.get('distancia_km', 0), 2)} km")

if inc.get("estado") not in ("TALLER_ASIGNADO", "ASIGNADO"):
    asig = post_json(f"{BASE}/incidentes/{INC_ID}/asignar", hconductor)
    print(f"Asignacion manual: {asig}")
else:
    print("El pipeline IA ya asignó taller automáticamente")

mis_asig = get_json(f"{BASE}/talleres/asignaciones", htaller)
items = mis_asig.get("items", [])
print(f"Asignaciones del taller: {len(items)}")
asig_id = None
for a in items:
    if a.get("incidente_id") == INC_ID and a.get("estado") == "ASIGNADO":
        asig_id = a["id"]
        print(f"  -> nuestra asig: {asig_id} estado={a['estado']}")
        break

if asig_id:
    print("\n=== FLUJO TECNICO ===")
    print(f"Taller aceptando asignacion {asig_id}")
    result = post_json(
        f"{BASE}/asignaciones/{asig_id}/aceptar",
        htaller,
        {"tecnico_id": "77777777-0000-0000-0000-000000000001"},
    )
    print(f"Aceptado: {result}")
else:
    print("NO SE ENCONTRO ASIGNACION PARA ESTE INCIDENTE")
