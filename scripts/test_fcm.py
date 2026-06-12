"""Envía push de prueba al conductor demo. Uso: python scripts/test_fcm.py"""
import asyncio
import sys

from sqlalchemy import text

sys.path.insert(0, "/app")

from app.core.db import scoped_session
from app.services.notifications import _init_firebase, send_push

TENANT = "22222222-0000-0000-0000-000000000000"


async def main() -> None:
    ok = _init_firebase()
    print("firebase init:", ok)
    if not ok:
        sys.exit(1)

    db = scoped_session(TENANT)
    row = db.execute(
        text(
            "SELECT email, fcm_token FROM emergencias.usuario "
            "WHERE email = 'carlos@mail.com'"
        )
    ).first()
    db.close()

    if not row or not row[1]:
        print("Sin fcm_token para carlos@mail.com — inicia sesión en el teléfono primero")
        sys.exit(1)

    token = row[1]
    print("enviando a", row[0], token[:24] + "…")
    await send_push(
        token,
        "Prueba Auxilio",
        "Push FCM desde backend OK",
        {"incidente_id": "test"},
    )
    print("send_push completado")


if __name__ == "__main__":
    asyncio.run(main())
