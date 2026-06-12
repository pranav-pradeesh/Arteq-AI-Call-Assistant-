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


def _base() -> str:
    return f"https://{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{settings.EXOTEL_API_KEY}"


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
