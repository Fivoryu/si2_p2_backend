from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from .db import make_session, SessionLocal
from .security import decode_token

bearer = HTTPBearer()


class CurrentUser:
    def __init__(self, id: str, rol: str, tenant: str | None, jti: str):
        self.id = id
        self.rol = rol
        self.tenant = tenant
        self.jti = jti

    @property
    def is_platform_admin(self) -> bool:
        return self.rol == "ADMIN_PLATAFORMA"


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
) -> CurrentUser:
    try:
        claims = decode_token(cred.credentials)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = CurrentUser(
        claims["sub"],
        claims["rol"],
        claims.get("tenant"),
        claims.get("jti", ""),
    )
    return user


def get_db(user: CurrentUser = Depends(get_current_user)):
    yield from make_session(user.tenant, user.is_platform_admin)


def get_db_public():
    """Session without tenant filter (login/register) or empty tenant."""
    yield from make_session(None, False)


def check_token_not_revoked(db, jti: str) -> None:
    if not jti:
        return
    row = db.execute(
        text("SELECT 1 FROM emergencias.token_revocado WHERE jti = :j"),
        {"j": jti},
    ).first()
    if row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revoked")


def get_current_user_verified(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
    db=Depends(get_db),
) -> CurrentUser:
    user = get_current_user(cred)
    check_token_not_revoked(db, user.jti)
    return user


def require_roles(*roles):
    def _guard(user: CurrentUser = Depends(get_current_user_verified)):
        if user.rol not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden for role")
        return user

    return _guard


# ------------------------------------------------------------------
# RBAC granular — PermissionService
# ------------------------------------------------------------------
# NOTA: get_permissions usa SessionLocal directamente (no get_db)
# porque get_db cierra la sesión al salir del yield, lo cual
# destruiría el PermissionService antes de que el endpoint lo use.
# En su lugar, abrimos una sesión manualmente y la cerramos en un
# wrapper que FastAPI puede usar como dependencia.

def _get_permissions_dep(
    user: CurrentUser = Depends(get_current_user_verified),
):
    from ..services.permissions import PermissionService

    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": user.tenant or ""},
        )
        svc = PermissionService(db, user)
        svc._load()
        yield svc, db
    finally:
        db.close()


def get_permissions(
    user: CurrentUser = Depends(get_current_user_verified),
):
    """Inyecta PermissionService + db. Se usa como:
       user, db, perm = Depends(get_permissions)
    """
    from ..services.permissions import PermissionService

    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": user.tenant or ""},
        )
        svc = PermissionService(db, user)
        svc._load()
        return svc, db
    except Exception:
        db.close()
        raise


def require_permission(entidad: str, accion: str):
    """Alternativa a require_roles() que verifica permisos RBAC.

    Yields (user, perm, db) — FastAPI cierra la sesion al finalizar el request.
    El caller debe usar perm.filter_dict() antes de devolver la respuesta.
    """
    def _guard(
        user: CurrentUser = Depends(get_current_user_verified),
    ):
        from ..services.permissions import PermissionService

        db = SessionLocal()
        try:
            db.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": user.tenant or ""},
            )
            svc = PermissionService(db, user)
            svc._load()
            if not svc.can(entidad, accion):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Requires '{accion}' on '{entidad}'",
                )
            yield user, svc, db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return _guard
