from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from .db import make_session
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


def require_roles(*roles):
    def _guard(user: CurrentUser = Depends(get_current_user)):
        if user.rol not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden for role")
        return user

    return _guard


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
