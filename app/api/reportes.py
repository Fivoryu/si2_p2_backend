from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.deps import CurrentUser, get_current_user_verified, get_db, require_permission

router = APIRouter(prefix="/reportes", tags=["reportes"])


class ConsultarIn(BaseModel):
    texto: str


@router.post("/consultar")
def consultar(
    body: ConsultarIn,
    user: CurrentUser = Depends(get_current_user_verified),
    db=Depends(get_db),
):
    from ..services.nl_reports import parse_nl_query
    from ..services.report_query import execute_report_query

    query = parse_nl_query(body.texto)
    result = execute_report_query(query, user.tenant, db)

    return {
        "query": query,
        "data": result["data"],
        "columns": result["columns"],
        "metric_label": result["metric_label"],
        "summary": result["summary"],
        "row_count": result["row_count"],
        "visualization": query.get("visualization", "kpi_card"),
    }
