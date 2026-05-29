from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
engine_admin = create_engine(settings.database_url_admin, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
SessionLocalAdmin = sessionmaker(bind=engine_admin, autoflush=False, autocommit=False)


def make_session(tenant_id: str | None, is_platform_admin: bool):
    if is_platform_admin:
        db = SessionLocalAdmin()
    else:
        db = SessionLocal()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": tenant_id or ""},
        )
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def scoped_session(tenant_id: str, is_platform_admin: bool = False):
    """Non-generator session for background tasks."""
    if is_platform_admin:
        return SessionLocalAdmin()
    db = SessionLocal()
    db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": tenant_id or ""},
    )
    return db
