import asyncio
import logging
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import scoped_session

logger = logging.getLogger(__name__)
_firebase_ready = False

ESTADO_NOTIFICATIONS = {
    "BUSCANDO_TALLER": (
        "Buscando taller",
        "Estamos buscando talleres disponibles cerca de tu ubicación.",
    ),
    "TALLER_ASIGNADO": (
        "Taller asignado",
        "Se asignó un taller a tu emergencia. Pronto recibirás ofertas o confirmación.",
    ),
    "EN_CAMINO": (
        "Técnico en camino",
        "El técnico está dirigiéndose a tu ubicación. ¡Estamos en camino!",
    ),
    "EN_ATENCION": (
        "Técnico llegó",
        "El técnico ha llegado al lugar del incidente y está atendiendo tu vehículo.",
    ),
    "FINALIZADO": (
        "Servicio finalizado",
        "El servicio ha sido completado. Por favor califica la atención recibida.",
    ),
}

OFFER_NOTIFICATION = (
    "Nueva oferta recibida",
    "Un taller envió una oferta para tu emergencia. Revisa las opciones disponibles.",
)

OFFER_SELECTED_NOTIFICATION = (
    "Oferta aceptada",
    "Se confirmó la oferta. El técnico va en camino hacia tu ubicación.",
)


def _init_firebase() -> bool:
    global _firebase_ready
    if _firebase_ready:
        return True

    path = settings.fcm_service_account_path.strip()
    if not path or not Path(path).is_file():
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(path))
        _firebase_ready = True
        logger.info("FCM HTTP v1 listo (service account: %s)", path)
        return True
    except Exception as exc:
        logger.warning("No se pudo inicializar Firebase Admin: %s", exc)
        return False


async def _send_push_v1(token: str, title: str, body: str, data: dict | None = None):
    from firebase_admin import messaging

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        token=token,
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, messaging.send, message)


async def _send_push_legacy(token: str, title: str, body: str, data: dict | None = None):
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


async def send_push(token: str, title: str, body: str, data: dict | None = None):
    if not token:
        return

    if _init_firebase():
        try:
            await _send_push_v1(token, title, body, data)
            return
        except Exception as exc:
            logger.warning("FCM v1 falló para token …%s: %s", token[-8:], exc)

    if settings.fcm_server_key:
        try:
            await _send_push_legacy(token, title, body, data)
        except Exception as exc:
            logger.warning("FCM legacy falló: %s", exc)


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
            """SELECT u.id, u.fcm_token
               FROM emergencias.incidente i
               JOIN emergencias.usuario u ON u.id = i.conductor_id
               WHERE i.id = :inc"""
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


async def notify_workshop_new_assignment(
    tenant_id: str, taller_id: str, incidente_id: str, taller_nombre: str
):
    db = scoped_session(tenant_id)
    try:
        row = db.execute(
            text(
                """SELECT u.fcm_token, u.id AS usuario_id
                FROM emergencias.usuario u
                JOIN emergencias.taller t ON t.usuario_id = u.id
                WHERE t.id = :tid"""
            ),
            {"tid": taller_id},
        ).mappings().first()

        if not row:
            return

        fcm_token: str | None = row.get("fcm_token")
        usuario_id = str(row["usuario_id"])

        title = "Nueva solicitud de auxilio"
        body = f"Tienes una nueva emergencia asignada: {taller_nombre}"
        data = {"incidente_id": incidente_id, "taller_id": taller_id}

        if fcm_token:
            await send_push(fcm_token, title, body, data)

        save_notification(
            db,
            tenant_id=tenant_id,
            usuario_id=usuario_id,
            incidente_id=incidente_id,
            titulo=title,
            mensaje=body,
            canal="PUSH",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def notify_estado_change(
    db: Session,
    tenant_id: str,
    incidente_id: str,
    estado_anterior: str,
    estado_nuevo: str,
):
    if estado_nuevo not in ESTADO_NOTIFICATIONS:
        return

    title, body = ESTADO_NOTIFICATIONS[estado_nuevo]
    await notify_incident_users(db, tenant_id, incidente_id, title, body)


async def notify_workshop_status_change(
    db: Session,
    tenant_id: str,
    taller_id: str,
    estado_nuevo: str,
    incidente_id: str,
):
    if estado_nuevo not in ESTADO_NOTIFICATIONS:
        return

    title, body = ESTADO_NOTIFICATIONS[estado_nuevo]
    title = f"[{title}]"

    row = db.execute(
        text(
            """SELECT u.fcm_token, u.id AS usuario_id
            FROM emergencias.usuario u
            JOIN emergencias.taller t ON t.usuario_id = u.id
            WHERE t.id = :tid"""
        ),
        {"tid": taller_id},
    ).mappings().first()

    if not row:
        return

    fcm_token: str | None = row.get("fcm_token")
    usuario_id = str(row["usuario_id"])

    if fcm_token:
        await send_push(fcm_token, title, body, {"incidente_id": incidente_id})

    save_notification(
        db,
        tenant_id=tenant_id,
        usuario_id=usuario_id,
        incidente_id=incidente_id,
        titulo=title,
        mensaje=body,
        canal="PUSH",
    )
