import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core.config import settings


async def send_push(token: str, title: str, body: str, data: dict | None = None):
    if not settings.fcm_server_key or not token:
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://fcm.googleapis.com/fcm/send",
            headers={
                "Authorization": f"key={settings.fcm_server_key}",
                "Content-Type": "application/json",
            },
            json={
                "to": token,
                "notification": {"title": title, "body": body},
                "data": data or {},
            },
            timeout=10,
        )


def save_notification(
    db: Session,
    *,
    tenant_id: str,
    usuario_id: str,
    incidente_id: str | None,
    titulo: str,
    mensaje: str,
    canal: str = "PUSH",
):
    db.execute(
        text(
            """INSERT INTO emergencias.notificacion
            (tenant_id, usuario_id, incidente_id, canal, titulo, mensaje, enviada)
            VALUES (:t, :u, :i, :c, :tit, :msg, true)"""
        ),
        {
            "t": tenant_id,
            "u": usuario_id,
            "i": incidente_id,
            "c": canal,
            "tit": titulo,
            "msg": mensaje,
        },
    )


async def notify_incident_users(
    db: Session, tenant_id: str, incidente_id: str, title: str, body: str
):
    rows = db.execute(
        text(
            """SELECT u.id, u.fcm_token FROM emergencias.usuario u
            JOIN emergencias.incidente i ON i.conductor_id = u.id OR i.id = :inc
            WHERE i.id = :inc AND u.fcm_token IS NOT NULL"""
        ),
        {"inc": incidente_id},
    ).mappings().all()
    for r in rows:
        if r.get("fcm_token"):
            await send_push(r["fcm_token"], title, body, {"incidente_id": incidente_id})
        save_notification(
            db,
            tenant_id=tenant_id,
            usuario_id=str(r["id"]),
            incidente_id=incidente_id,
            titulo=title,
            mensaje=body,
        )
