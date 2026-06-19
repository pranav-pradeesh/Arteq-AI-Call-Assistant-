"""
Vobiz call recording service.

Vobiz exposes a full recording API:
  Base:     https://api.vobiz.ai/api/v1
  Auth:     X-Auth-ID + X-Auth-Token headers
  Download: https://media.vobiz.ai/v1/Account/{auth_id}/Recording/{id}.mp3

If VOBIZ_RECORD_CALLS is False (default) every method is a no-op so callers
never crash when recording is disabled.
"""
from __future__ import annotations

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_API = "https://api.vobiz.ai/api/v1"
_MEDIA = "https://media.vobiz.ai/v1"


def _headers() -> dict:
    return {
        "X-Auth-ID": settings.VOBIZ_API_KEY,
        "X-Auth-Token": settings.VOBIZ_API_SECRET,
        "Content-Type": "application/json",
    }


def _configured() -> bool:
    return bool(settings.VOBIZ_RECORD_CALLS and settings.VOBIZ_API_KEY and settings.VOBIZ_API_SECRET)


async def start_recording(call_uuid: str) -> str | None:
    """Start recording a live Vobiz call. Returns recording_uuid or None."""
    if not _configured():
        return None
    url = f"{_API}/Account/{settings.VOBIZ_API_KEY}/Call/{call_uuid}/Record/"
    payload = {
        "format": settings.VOBIZ_RECORDING_FORMAT,
        "channels": settings.VOBIZ_RECORDING_CHANNELS,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code in (200, 201):
            data = resp.json()
            rec_id = data.get("RecordingID") or data.get("recording_uuid") or ""
            logger.info("vobiz_recording_started", call_uuid=call_uuid, recording_id=rec_id)
            return rec_id or True  # True signals success even without a returned ID
        logger.warning("vobiz_recording_start_failed",
                       call_uuid=call_uuid, status=resp.status_code, body=resp.text[:200])
    except Exception as exc:
        logger.error("vobiz_recording_start_error", call_uuid=call_uuid, error=str(exc))
    return None


async def list_recordings(
    limit: int = 50,
    offset: int = 0,
    call_uuid: str | None = None,
) -> list[dict]:
    """List recordings for this Vobiz account. Returns list of recording dicts."""
    if not _configured():
        return []
    url = f"{_API}/Account/{settings.VOBIZ_API_KEY}/Recording/"
    params: dict = {"limit": limit, "offset": offset}
    if call_uuid:
        params["call_uuid"] = call_uuid
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=_headers())
        if resp.status_code == 200:
            data = resp.json()
            objects = data.get("objects") or data.get("recordings") or []
            return [_enrich(r) for r in objects]
        logger.warning("vobiz_recordings_list_failed", status=resp.status_code, body=resp.text[:200])
    except Exception as exc:
        logger.error("vobiz_recordings_list_error", error=str(exc))
    return []


async def get_recording(recording_id: str) -> dict | None:
    """Fetch metadata for a single recording."""
    if not settings.VOBIZ_API_KEY:
        return None
    url = f"{_API}/Account/{settings.VOBIZ_API_KEY}/Recording/{recording_id}/"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return _enrich(resp.json())
        logger.warning("vobiz_recording_get_failed", recording_id=recording_id, status=resp.status_code)
    except Exception as exc:
        logger.error("vobiz_recording_get_error", recording_id=recording_id, error=str(exc))
    return None


def recording_download_url(recording_id: str, fmt: str = "mp3") -> str:
    """Build the authenticated download URL for a recording."""
    return f"{_MEDIA}/Account/{settings.VOBIZ_API_KEY}/Recording/{recording_id}.{fmt}"


def _enrich(rec: dict) -> dict:
    """Add a pre-signed download URL to a recording dict."""
    rid = rec.get("RecordingID") or rec.get("recording_id") or rec.get("id") or ""
    if rid and "download_url" not in rec:
        fmt = settings.VOBIZ_RECORDING_FORMAT
        rec["download_url"] = recording_download_url(str(rid), fmt)
    return rec
