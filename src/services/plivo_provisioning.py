"""
Plivo phone number provisioning — buy, configure, and release Indian DIDs.

Each hospital gets one Plivo DID that routes inbound calls to:
  POST https://<render-url>/api/v1/call/inbound/<slug>
which returns Plivo PCML XML that opens a WebSocket stream at:
  wss://<render-url>/ws/call/<slug>
"""
from __future__ import annotations

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


def _base() -> str:
    return f"https://api.plivo.com/v1/Account/{settings.PLIVO_AUTH_ID}"


def _auth() -> tuple[str, str]:
    return (settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN)


async def search_india_numbers(count: int = 5, number_type: str = "local") -> list[str]:
    """Return available Indian phone numbers that can be purchased."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_base()}/PhoneNumber/",
            params={"country_iso": "IN", "type": number_type, "limit": count},
            auth=_auth(),
        )
    if resp.status_code != 200:
        logger.error("plivo_search_failed", status=resp.status_code, body=resp.text[:200])
        return []
    data = resp.json()
    return [obj["number"] for obj in data.get("objects", [])]


async def buy_number(number: str) -> bool:
    """Rent a phone number from Plivo."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_base()}/PhoneNumber/{number}/",
            auth=_auth(),
        )
    if resp.status_code in (200, 201):
        logger.info("plivo_number_bought", number=number[-4:])
        return True
    logger.error("plivo_buy_failed",
                 number=number[-4:], status=resp.status_code, body=resp.text[:200])
    return False


async def configure_number_for_hospital(number: str, slug: str) -> bool:
    """Point a Plivo number's inbound webhook at the hospital's slug."""
    answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{slug}"
    hangup_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/status"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_base()}/Number/{number}/",
            data={
                "answer_url": answer_url,
                "answer_method": "POST",
                "hangup_url": hangup_url,
                "hangup_method": "POST",
            },
            auth=_auth(),
        )
    if resp.status_code in (200, 201, 202):
        logger.info("plivo_number_configured", number=number[-4:], slug=slug)
        return True
    logger.error("plivo_configure_failed",
                 number=number[-4:], status=resp.status_code, body=resp.text[:200])
    return False


async def provision_number_for_hospital(slug: str) -> str | None:
    """
    Full provision flow: search → buy → configure.
    Returns the E.164 number string on success, None on failure.
    """
    if not settings.PLIVO_AUTH_ID or not settings.PLIVO_AUTH_TOKEN:
        logger.warning("plivo_not_configured_skipping_provision")
        return None

    numbers = await search_india_numbers(count=5)
    if not numbers:
        logger.error("plivo_no_india_numbers_available")
        return None

    for candidate in numbers:
        if await buy_number(candidate):
            if await configure_number_for_hospital(candidate, slug):
                return candidate
            logger.warning("plivo_configure_failed_after_buy", number=candidate[-4:])
    return None


async def reconfigure_number(number: str, new_slug: str) -> bool:
    """Update an already-owned number to point to a different hospital slug."""
    return await configure_number_for_hospital(number, new_slug)


async def release_number(number: str) -> bool:
    """Return a phone number to Plivo (used when a hospital subscription ends)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{_base()}/Number/{number}/",
            auth=_auth(),
        )
    if resp.status_code in (200, 204):
        logger.info("plivo_number_released", number=number[-4:])
        return True
    logger.error("plivo_release_failed",
                 number=number[-4:], status=resp.status_code, body=resp.text[:200])
    return False


async def list_owned_numbers() -> list[dict]:
    """List all Plivo numbers on this account with their current configuration."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{_base()}/Number/", auth=_auth())
    if resp.status_code != 200:
        return []
    return resp.json().get("objects", [])
