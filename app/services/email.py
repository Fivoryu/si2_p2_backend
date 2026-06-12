import logging

try:
    import resend
except ImportError:
    resend = None

from ..core.config import settings

logger = logging.getLogger(__name__)

SES_SENDER = "Auxilio App <{}>".format("noreply@auxilio.app")


def send_temp_password_email(to_email: str, nombre: str, temp_password: str) -> bool:
    """Dispatch email based on email_provider config: console | ses | resend."""
    provider = settings.email_provider.lower()
    if provider == "ses":
        return _send_via_ses(to_email, nombre, temp_password)
    elif provider == "resend":
        return _send_via_resend(to_email, nombre, temp_password)
    else:
        return _send_via_console(to_email, nombre, temp_password)


def _send_via_console(to_email: str, nombre: str, temp_password: str) -> bool:
    logger.info(
        f"[DEV] Temp password for {to_email}: {temp_password}\n"
        f"       Subject: Tu cuenta en Auxilio App - Contraseña temporal"
    )
    return False


def _send_via_ses(to_email: str, nombre: str, temp_password: str) -> bool:
    try:
        import boto3

        client = boto3.client(
            "ses",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            endpoint_url=settings.aws_endpoint_url or None,
        )
        sender = f"Auxilio App <{settings.ses_sender_email}>"
        html_body = (
            f"<h2>Hola {nombre}</h2>"
            f"<p>Tu red de talleres ha sido creada en <strong>Auxilio App</strong>.</p>"
            f"<p>Tu contraseña temporal es:</p>"
            f"<div style='background:#f4f4f4;padding:12px;font-size:20px;"
            f"font-family:monospace;border-radius:6px;margin:16px 0'>"
            f"<strong>{temp_password}</strong></div>"
            f"<p>Deberás cambiarla al iniciar sesión.</p>"
            f"<p><a href='http://localhost:4200/login'>Ir a Auxilio App</a></p>"
        )
        client.send_email(
            Source=sender,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {
                    "Data": "Tu cuenta en Auxilio App - Contraseña temporal",
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        logger.info(f"SES email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"SES failed for {to_email}: {e}")
        logger.info(f"[DEV] Temp password for {to_email}: {temp_password}")
        return False


def _send_via_resend(to_email: str, nombre: str, temp_password: str) -> bool:
    if not settings.resend_api_key or resend is None:
        logger.info(f"[DEV] Temp password for {to_email}: {temp_password}")
        return False
    resend.api_key = settings.resend_api_key
    sender = settings.resend_sender_email or "onboarding@resend.dev"
    try:
        resend.Emails.send(
            {
                "from": f"Auxilio App <{sender}>",
                "to": to_email,
                "subject": "Tu cuenta en Auxilio App - Contraseña temporal",
                "html": (
                    f"<h2>Hola {nombre}</h2>"
                    f"<p>Tu red de talleres ha sido creada en <strong>Auxilio App</strong>.</p>"
                    f"<p>Tu contraseña temporal es:</p>"
                    f"<div style='background:#f4f4f4;padding:12px;font-size:20px;"
                    f"font-family:monospace;border-radius:6px;margin:16px 0'>"
                    f"<strong>{temp_password}</strong></div>"
                    f"<p>Deberás cambiarla al iniciar sesión.</p>"
                    f"<p><a href='http://localhost:4200/login'>Ir a Auxilio App</a></p>"
                ),
            }
        )
        logger.info(f"Resend email sent to {to_email}")
        return True
    except Exception as e:
        logger.warning(f"Resend failed for {to_email}: {e}")
        logger.info(f"[DEV] Temp password for {to_email}: {temp_password}")
        return False
