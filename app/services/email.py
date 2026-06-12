import logging

try:
    import resend
except ImportError:
    resend = None

from ..core.config import settings

logger = logging.getLogger(__name__)


def send_temp_password_email(to_email: str, nombre: str, temp_password: str) -> bool:
    """Send temporary password via Resend. Returns True if sent, False if dev-only."""
    if not settings.resend_api_key or resend is None:
        logger.info(f"[DEV] Temp password for {to_email}: {temp_password}")
        return False
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send(
            {
                "from": "Auxilio App <onboarding@resend.dev>",
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
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        # Resend test mode only allows sending to verified addresses.
        # Fall back to console log so webhook doesn't fail.
        logger.warning(f"Resend failed for {to_email}: {e}")
        logger.info(f"[DEV] Temp password for {to_email}: {temp_password}")
        return False
