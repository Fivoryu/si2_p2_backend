from __future__ import annotations

from sqlalchemy import text

from ..core.deps import CurrentUser


def can_access_incident(db, incidente_id: str, user: CurrentUser) -> bool:
    if user.is_platform_admin:
        return True
    if user.rol == "ADMIN_TENANT":
        return True
    if user.rol == "CONDUCTOR":
        row = db.execute(
            text(
                """SELECT 1 FROM emergencias.incidente
                   WHERE id = :i AND conductor_id = :u"""
            ),
            {"i": incidente_id, "u": user.id},
        ).first()
        return row is not None
    if user.rol == "TALLER":
        row = db.execute(
            text(
                """SELECT 1
                   FROM emergencias.asignacion a
                   JOIN emergencias.taller t ON t.id = a.taller_id
                   WHERE a.incidente_id = :i AND t.usuario_id = :u"""
            ),
            {"i": incidente_id, "u": user.id},
        ).first()
        return row is not None
    if user.rol == "TECNICO":
        row = db.execute(
            text(
                """SELECT 1
                   FROM emergencias.asignacion a
                   JOIN emergencias.tecnico tec ON tec.id = a.tecnico_id
                   WHERE a.incidente_id = :i AND tec.usuario_id = :u"""
            ),
            {"i": incidente_id, "u": user.id},
        ).first()
        return row is not None
    return False


def can_manage_incident_service(db, incidente_id: str, user: CurrentUser) -> bool:
    if user.is_platform_admin or user.rol == "ADMIN_TENANT":
        return True
    if user.rol == "TALLER":
        row = db.execute(
            text(
                """SELECT 1
                   FROM emergencias.asignacion a
                   JOIN emergencias.taller t ON t.id = a.taller_id
                   WHERE a.incidente_id = :i
                     AND a.estado = 'ACEPTADO'
                     AND t.usuario_id = :u"""
            ),
            {"i": incidente_id, "u": user.id},
        ).first()
        return row is not None
    if user.rol == "TECNICO":
        row = db.execute(
            text(
                """SELECT 1
                   FROM emergencias.asignacion a
                   JOIN emergencias.tecnico tec ON tec.id = a.tecnico_id
                   WHERE a.incidente_id = :i
                     AND a.estado = 'ACEPTADO'
                     AND tec.usuario_id = :u"""
            ),
            {"i": incidente_id, "u": user.id},
        ).first()
        return row is not None
    return False


def best_tecnico_for_assignment(db, taller_id: str, tipo_incidente_id: str | None = None) -> str | None:
    tipo_filter = ""
    params: dict = {"tl": taller_id}
    if tipo_incidente_id:
        tipo_filter = """
            ORDER BY
              CASE WHEN EXISTS (
                SELECT 1
                FROM emergencias.tecnico_especialidad te
                JOIN emergencias.especialidad_taller et ON et.id = te.especialidad_id
                JOIN emergencias.tipo_incidente ti
                  ON lower(et.nombre) LIKE '%' || lower(split_part(ti.nombre, ' ', 1)) || '%'
                WHERE te.tecnico_id = tec.id AND ti.id = :tipo
              ) THEN 0 ELSE 1 END,
              active_jobs ASC,
              tec.nombre ASC
        """
        params["tipo"] = tipo_incidente_id
    else:
        tipo_filter = "ORDER BY active_jobs ASC, tec.nombre ASC"

    row = db.execute(
        text(
            f"""SELECT tec.id,
                       (SELECT count(*)
                        FROM emergencias.asignacion a
                        WHERE a.tecnico_id = tec.id
                          AND a.estado IN ('ASIGNADO', 'ACEPTADO')) AS active_jobs
                FROM emergencias.tecnico tec
                WHERE tec.taller_id = :tl AND tec.disponible = true
                {tipo_filter}
                LIMIT 1"""
        ),
        params,
    ).mappings().first()
    return str(row["id"]) if row else None


def sql_user_owns_taller_asignacion(user: CurrentUser, asignacion_alias: str = "a") -> tuple[str, dict]:
    """Fragmento SQL + params: el usuario puede actuar en nombre del taller de la asignación."""
    if user.rol == "TALLER":
        return (
            f"""EXISTS (
                SELECT 1 FROM emergencias.taller t
                WHERE t.id = {asignacion_alias}.taller_id AND t.usuario_id = :uid
            )""",
            {"uid": user.id},
        )
    if user.rol == "TECNICO":
        return (
            f"""EXISTS (
                SELECT 1 FROM emergencias.tecnico tec
                WHERE tec.taller_id = {asignacion_alias}.taller_id
                  AND tec.usuario_id = :uid
            )""",
            {"uid": user.id},
        )
    if user.rol == "ADMIN_TENANT":
        return (
            f"{asignacion_alias}.tenant_id = :tid",
            {"tid": user.tenant},
        )
    return "FALSE", {}
