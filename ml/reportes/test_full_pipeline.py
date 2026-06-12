import sys, json
sys.path.insert(0, "/app")
from sqlalchemy import create_engine, text
from app.services.nl_reports import parse_nl_query
from app.services.report_query import execute_report_query, build_sql

# Conectar a la BD
url = "postgresql+psycopg2://postgres:postgres@db:5432/emergencias_db"
engine = create_engine(url)

# Obtener tenant_id
with engine.connect() as conn:
    row = conn.execute(text("SELECT id FROM emergencias.tenant LIMIT 1")).fetchone()
    tenant_id = str(row[0])

print(f"Tenant: {tenant_id}\n")

tests = [
    "¿Cuántos incidentes hay en total?",
    "¿Cuántos incidentes de batería hay?",
    "¿Cuál es el tiempo promedio de llegada por taller?",
    "Lista los talleres con más incidentes",
]

with engine.connect() as conn:
    for q_text in tests:
        query = parse_nl_query(q_text)
        print(f"IN:  {q_text}")
        print(f"QUERY: {json.dumps({k:v for k,v in query.items() if k != 'original_text'}, ensure_ascii=False)}")

        sql, params = build_sql(query, tenant_id)
        print(f"SQL: {sql}")
        print(f"PARAMS: {params}")

        result = execute_report_query(query, tenant_id, conn)
        print(f"ROWS: {result['row_count']}")
        print(f"DATA: {result['data'][:3]}")
        print(f"SUMMARY: {result['summary']}")
        print()
