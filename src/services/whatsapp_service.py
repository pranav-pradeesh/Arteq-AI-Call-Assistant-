"""
WhatsApp Service — sends patient notifications over WhatsApp via Plivo.

Subclasses SMSService so every message body (confirmation, cancellation,
callback, token-active, …) is defined in ONE place; only the transport changes.
If WhatsApp isn't configured or a send fails, it falls back to SMS so a patient
is never left without notice.

Use get_messenger() to pick the right channel from settings.
"""
from __future__ import annotations

import httpx
import structlog

from src.config.settings import settings
from src.services.sms_service import SMSService

logger = structlog.get_logger(__name__)


class WhatsAppService(SMSService):
    """Sends WhatsApp messages via Plivo's omnichannel Messages API."""

    _WA_URL = "https://api.plivo.com/v1/Account/{auth_id}/Messages/"

    async def _send(self, phone: str, message: str) -> bool:
        sender = settings.PLIVO_WHATSAPP_NUMBER or settings.PLIVO_PHONE_NUMBER
        if not settings.PLIVO_AUTH_ID or not sender:
            logger.warning("whatsapp_skipped_not_configured")
            return await self._sms_fallback(phone, message)

        url = self._WA_URL.format(auth_id=settings.PLIVO_AUTH_ID)
        payload = {
            "src": sender,
            "dst": phone,
            "text": message,
            "type": "whatsapp",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    auth=(settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN),
                )
            if response.status_code in (200, 201, 202):
                logger.info("whatsapp_sent", phone=phone[:6] + "****", msg_len=len(message))
                return True
            logger.warning(
                "whatsapp_failed",
                phone=phone[:6] + "****",
                status_code=response.status_code,
                error=response.text[:200],
            )
        except Exception as exc:
            logger.error("whatsapp_failed", error=str(exc), phone=phone[:6] + "****")
        return await self._sms_fallback(phone, message)

    async def _sms_fallback(self, phone: str, message: str) -> bool:
        if not settings.WHATSAPP_FALLBACK_TO_SMS:
            return False
        logger.info("whatsapp_fallback_to_sms", phone=phone[:6] + "****")
        return await super()._send(phone, message)


def get_messenger() -> SMSService:
    """Return the configured patient-notification channel.

    WhatsApp when WHATSAPP_ENABLED (with SMS fallback baked in), else plain SMS.
    Both expose the same method surface (SMSService)."""
    if settings.WHATSAPP_ENABLED:
        return WhatsAppService()
    return SMSService()
