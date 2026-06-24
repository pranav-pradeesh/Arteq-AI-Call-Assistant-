"""
WhatsApp notifications via Meta's WhatsApp Cloud API.

This is the ONLY patient-messaging channel (no SMS — Vobiz is SIP-only for
voice). Business-initiated messages on WhatsApp must use pre-approved
*templates*, so each notification maps to a template name + ordered body
parameters. Free-text (`_send`) is only delivered inside the 24-hour
customer-service window and is used for in-conversation replies.

Setup (one-time, in Meta Business Manager):
  1. Create a WhatsApp Business app, get a permanent access token + phone
     number id  →  WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID.
  2. Create these "Utility" templates with body variables in the order shown
     in each method below, in your language (WHATSAPP_TEMPLATE_LANG).
  3. Set WHATSAPP_ENABLED=true.

If WhatsApp is not configured, get_messenger() returns the no-op SMSService
base so callers never crash.
"""
from __future__ import annotations

import re

import httpx
import structlog

from src.config.settings import settings
from src.services.sms_service import SMSService

logger = structlog.get_logger(__name__)


def _to_msisdn(phone: str) -> str:
    """Meta expects the recipient as digits in international format, no '+'."""
    return re.sub(r"\D", "", phone or "")


class WhatsAppService(SMSService):
    """Sends patient notifications over WhatsApp (Meta Cloud API).

    Subclasses SMSService so the public method surface (send_appointment_
    confirmation, send_appointment_reminder, …) is identical — call sites
    don't change. Each method sends the matching approved template.
    """

    @property
    def _url(self) -> str:
        return (
            f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
            f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }

    def _configured(self) -> bool:
        return bool(
            settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_ACCESS_TOKEN
        )

    async def _post(self, payload: dict, *, kind: str) -> bool:
        if not self._configured():
            logger.warning("whatsapp_skipped_not_configured", kind=kind)
            return False
        to = payload.get("to", "")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._url, json=payload, headers=self._headers)
            if resp.status_code in (200, 201):
                logger.info("whatsapp_sent", kind=kind, phone=to[:6] + "****")
                return True
            logger.warning(
                "whatsapp_failed", kind=kind, status_code=resp.status_code,
                error=resp.text[:300],
            )
            return False
        except Exception as exc:
            logger.error("whatsapp_failed", kind=kind, error=str(exc))
            return False

    async def _send_template(self, phone: str, template: str, params: list) -> bool:
        """Send an approved template with ordered body text parameters."""
        components = []
        if params:
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }]
        payload = {
            "messaging_product": "whatsapp",
            "to": _to_msisdn(phone),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
                "components": components,
            },
        }
        return await self._post(payload, kind=template)

    async def _send(self, phone: str, message: str) -> bool:
        """Free-text message — delivered only inside the 24h session window."""
        payload = {
            "messaging_product": "whatsapp",
            "to": _to_msisdn(phone),
            "type": "text",
            "text": {"preview_url": True, "body": message},
        }
        return await self._post(payload, kind="freetext")

    # ── Template-backed notifications ──────────────────────────────────────
    # Body variable order is documented per method; the Meta template must
    # use {{1}}, {{2}}, … in the SAME order.

    async def send_appointment_confirmation(
        self, phone: str, hospital_name: str, patient_name: str,
        doctor_name: str, date: str, time: str, code: str = "",
    ) -> bool:
        # {{1}} patient {{2}} hospital {{3}} doctor {{4}} date {{5}} time {{6}} code
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_CONFIRMATION,
            [patient_name, hospital_name, f"Dr. {doctor_name}", date, time, code or "-"],
        )

    async def send_token_active(
        self, phone: str, hospital_name: str, patient_name: str,
        doctor_name: str, date: str, time: str, token_number: int,
    ) -> bool:
        # {{1}} patient {{2}} hospital {{3}} doctor {{4}} date {{5}} time {{6}} token
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_TOKEN_ACTIVE,
            [patient_name, hospital_name, f"Dr. {doctor_name}", date, time, str(token_number)],
        )

    async def send_appointment_reminder(
        self, phone: str, hospital_name: str, patient_name: str,
        doctor_name: str, date: str, time: str,
    ) -> bool:
        # {{1}} patient {{2}} hospital {{3}} doctor {{4}} date {{5}} time
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_REMINDER,
            [patient_name, hospital_name, f"Dr. {doctor_name}", date, time],
        )

    async def send_appointment_cancellation(
        self, phone: str, hospital_name: str, patient_name: str,
        doctor_name: str, date: str,
    ) -> bool:
        # {{1}} patient {{2}} hospital {{3}} doctor {{4}} date
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_CANCELLATION,
            [patient_name, hospital_name, f"Dr. {doctor_name}", date],
        )

    async def send_doctor_availability(
        self, phone: str, hospital_name: str, patient_name: str,
        doctor_name: str, date: str, status: str,
    ) -> bool:
        _STATUS = {
            "available": "is available and ready to see you",
            "delayed": "is running slightly delayed",
            "unavailable": "is unavailable — please contact us to reschedule",
        }
        # {{1}} patient {{2}} hospital {{3}} doctor {{4}} status {{5}} date
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_DOCTOR_AVAIL,
            [patient_name, hospital_name, f"Dr. {doctor_name}",
             _STATUS.get(status, "availability update"), date],
        )

    async def send_callback_confirmation(
        self, phone: str, hospital_name: str, preferred_time: str,
    ) -> bool:
        # {{1}} hospital {{2}} preferred_time
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_CALLBACK,
            [hospital_name, preferred_time],
        )

    async def send_maps_link(
        self, phone: str, hospital_name: str, address: str,
    ) -> bool:
        import urllib.parse
        maps_url = f"https://maps.google.com/?q={urllib.parse.quote(address)}"
        # {{1}} hospital {{2}} address {{3}} maps_url
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_LOCATION,
            [hospital_name, address, maps_url],
        )

    async def send_lab_schedule(
        self, phone: str, hospital_name: str, test_name: str,
        instructions: str, lab_timing: str,
    ) -> bool:
        # {{1}} hospital {{2}} test {{3}} timing {{4}} instructions
        return await self._send_template(
            phone, settings.WHATSAPP_TPL_LAB,
            [hospital_name, test_name, lab_timing, instructions],
        )


class _WhatsAppThenSMS:
    """WhatsApp first; if a send fails (or WhatsApp is misconfigured at runtime),
    fall back to plain SMS. Duck-types SMSService — every send_* notification is
    tried on WhatsApp, then on SMS. Non-send attributes delegate to the SMS base.
    """

    def __init__(self) -> None:
        self._wa = WhatsAppService()
        self._sms = SMSService()

    def __getattr__(self, name: str):
        if not name.startswith("send_"):
            return getattr(self._sms, name)

        async def _try(*args, **kwargs) -> bool:
            try:
                if await getattr(self._wa, name)(*args, **kwargs):
                    return True
            except Exception as exc:  # WhatsApp transport error -> fall back
                logger.warning("whatsapp_send_error_falling_back", method=name, error=str(exc))
            return await getattr(self._sms, name)(*args, **kwargs)

        return _try


def get_messenger() -> SMSService:
    """Return the patient-notification channel.

    - WhatsApp configured  -> WhatsApp first, SMS fallback on failure.
    - WhatsApp not available -> SMS (real send if SMS_PROVIDER is set, else no-op).
    """
    wa_ready = (
        settings.WHATSAPP_ENABLED
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_ACCESS_TOKEN
    )
    if wa_ready:
        return _WhatsAppThenSMS()  # type: ignore[return-value]
    return SMSService()
