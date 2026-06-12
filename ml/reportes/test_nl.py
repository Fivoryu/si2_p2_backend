import sys, json
sys.path.insert(0, "/app")
from app.services.nl_reports import parse_nl_query

tests = [
    "¿Cuántos incidentes de batería hubo en junio?",
    "¿Cuál es el tiempo promedio de llegada por taller?",
    "Muéstrame los incidentes de choque en el mapa",
    "¿Cómo evolucionaron los incidentes el último mes?",
    "¿Cuáles son los 5 talleres con mejor calificación?",
    "Compara el tiempo de respuesta entre taller A y B",
    "Lista los pagos de la última semana",
    "¿Por qué aumentaron los incidentes de llanta?",
]

for t in tests:
    r = parse_nl_query(t)
    print(f"IN:  {t}")
    print(f"OUT: intent={r['intent']}  entity={r['entity']}  metric={r['metric']}  viz={r['visualization']}  conf={r['confidence']}")
    print()
