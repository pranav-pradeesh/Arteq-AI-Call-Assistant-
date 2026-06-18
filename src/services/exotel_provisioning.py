"""
Exotel phone number (ExoPhone) provisioning.

Exotel does not provide a self-serve number purchase API — ExoPhones are
assigned by Exotel support. This module handles:
  • Configuring an assigned ExoPhone's webhook URL to point at a hospital slug
  • Reconfiguring / releasing (marking unused) an ExoPhone
  • Listing ExoPhones on the account

The webhook URL format is:
  POST https://<render-url>/api/v1/call/inbound/exotel/<token>/<slug>

Where <token> matches EXOTEL_WEBHOOK_TOKEN (embedded secret so the URL itself
is hard to guess — Exotel does not send a cryptographic signature).
"""
from __future__ import annotations

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


def _account_sid() -> str:
    """Exotel Account SID used in the API URL path.

    Newer Exotel accounts issue a distinct API Key, API Token and Account SID;
    the SID (e.g. "arteqai3") is what goes in the URL while the Key/Token are
    the HTTP Basic credentials. Older accounts used the API Key as the SID, so
    fall back to EXOTEL_API_KEY when EXOTEL_ACCOUNT_SID is unset.
    """
    return settings.EXOTEL_ACCOUNT_SID or settings.EXOTEL_API_KEY


def _base() -> str:
    return f"https://{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{_account_sid()}"


def _auth() -> tuple[str, str]:
    return (settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN)


def _webhook_url(slug: str) -> str:
    token = settings.EXOTEL_WEBHOOK_TOKEN or "default"
    return f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/exotel/{token}/{slug}"


async def configure_number_for_hospital(number: str, slug: str) -> bool:
    """Point an ExoPhone's inbound webhook at the hospital's slug.

    Exotel maps a Virtual Number to an "App". We update the number's VoiceUrl
    so every answered call POSTs to our webhook.
    """
    answer_url = _webhook_url(slug)
    # Exotel uses the number without country code prefix in the path on some
    # regions; the API accepts the number in E.164 (with +) in the body.
    phone = number.lstrip("+")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_base()}/Numbers/{phone}.json",
            data={
                "VoiceUrl": answer_url,
                "VoiceMethod": "POST",
                "StatusCallback": f"{settings.PUBLIC_BASE_URL}/api/v1/call/status",
                "StatusCallbackMethod": "POST",
            },
            auth=_auth(),
        )
    if resp.status_code in (200, 201, 202):
        logger.info("exotel_number_configured", number=number[-4:], slug=slug)
        return True
    logger.error(
        "exotel_configure_failed",
        number=number[-4:],
        status=resp.status_code,
        body=resp.text[:200],
    )
    return False


async def reconfigure_number(number: str, new_slug: str) -> bool:
    """Update an ExoPhone to point to a different hospital slug."""
    return await configure_number_for_hospital(number, new_slug)


async def connect_call_to_voicebot(patient_phone: str, room: str) -> bool:
    """Place an outbound call that streams over the Voicebot WebSocket.

    Exotel dials `patient_phone` from the ExoPhone and connects it to the App
    in EXOTEL_VOICEBOT_APP_ID, whose Voicebot applet streams audio to our WS.
    The pre-created LiveKit `room` is forwarded via `CustomField` so the bridge
    joins the right room (it surfaces in the start event's custom_parameters).

    Returns True if Exotel accepted the call request.
    """
    if not settings.EXOTEL_VOICEBOT_APP_ID:
        logger.error("exotel_voicebot_app_id_unset")
        return False
    if not settings.EXOTEL_PHONE_NUMBER:
        logger.error("exotel_phone_number_unset")
        return False

    phone = patient_phone if patient_phone.startswith("+") else f"+{patient_phone}"
    # Applet path is keyed by the Account SID (same identifier as the REST base),
    # not the API Key — on newer Exotel accounts the two differ, so using the key
    # here 404s/mis-routes. Use https for the same reason _base() does.
    app_url = (
        f"https://my.exotel.com/{_account_sid()}"
        f"/exoml/start_voice/{settings.EXOTEL_VOICEBOT_APP_ID}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_base()}/Calls/connect.json",
            data={
                "From": phone,
                "CallerId": settings.EXOTEL_PHONE_NUMBER,
                "Url": app_url,
                "CustomField": room,
                "StatusCallback": f"{settings.PUBLIC_BASE_URL}/api/v1/call/status",
            },
            auth=_auth(),
        )
    if resp.status_code in (200, 201, 202):
        logger.info("exotel_voicebot_call_placed", patient=phone[-4:], room=room)
        return True
    logger.error(
        "exotel_voicebot_call_failed",
        patient=phone[-4:],
        status=resp.status_code,
        body=resp.text[:200],
    )
    return False


async def list_owned_numbers() -> list[dict]:
    """List all ExoPhones on this Exotel account."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{_base()}/Numbers.json", auth=_auth())
    if resp.status_code != 200:
        logger.warning("exotel_list_numbers_failed", status=resp.status_code)
        return []
    data = resp.json()
    # Exotel wraps results in {"TwilioResponse": {"Numbers": {"Number": [...]}}}
    try:
        numbers = data.get("TwilioResponse", {}).get("Numbers", {}).get("Number", [])
        if isinstance(numbers, dict):
            numbers = [numbers]
        return numbers
    except Exception:
        return []
