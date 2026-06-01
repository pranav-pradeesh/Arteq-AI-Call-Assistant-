"""
Outbound Calls — proactive reminder, confirmation, callback, followup,
and campaign calls via Plivo.

Plivo dials the patient; once answered it POSTs to our answer_url webhook,
which returns PCML directing Plivo to open a bidirectional audio stream to
our WebSocket handler where Arya handles the conversation.
"""
from __future__ import annotations

import json
from datetime import datetime

import httpx
import structlog

from src.cache.store import session_cache
from src.config.settings import settings

logger = structlog.get_logger(__name__)

try:
    import pytz as _pytz
    _INDIA_TZ = _pytz.timezone("Asia/Kolkata")
except ImportError:
    _INDIA_TZ = None  # type: ignore[assignment]


def _plivo_call_url() -> str:
    return f"https://api.plivo.com/v1/Account/{settings.PLIVO_AUTH_ID}/Call/"


def _plivo_auth() -> tuple[str, str]:
    return (settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN)


async def _place_call(
    to: str,
    answer_url: str,
    context: dict,
    time_limit: int = 180,
) -> bool:
    """
    Place a Plivo outbound call and cache the context by request_uuid so the
    answer_url webhook can pass it to the WebSocket handler.

    Returns True on success, False on failure.
    """
    if not settings.PLIVO_AUTH_ID or not settings.PLIVO_AUTH_TOKEN:
        logger.warning("plivo_not_configured_skipping_outbound")
        return False

    hangup_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/status"
    payload = {
        "from": settings.PLIVO_PHONE_NUMBER,
        "to": to,
        "answer_url": answer_url,
        "answer_method": "POST",
        "hangup_url": hangup_url,
        "hangup_method": "POST",
        "time_limit": str(time_limit),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _plivo_call_url(),
                json=payload,
                auth=_plivo_auth(),
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            request_uuid = data.get("request_uuid", "")
            if request_uuid:
                # Store context so the answer_url webhook can look it up by request_uuid,
                # then transfer it to a call_uuid key for the WebSocket handler.
                session_cache.set(f"plivo:{request_uuid}", context, ttl=600)
            logger.info(
                "outbound_call_placed",
                patient=to[-4:],
                call_type=context.get("call_type"),
                request_uuid=(request_uuid[-8:] if request_uuid else "?"),
            )
            return True
        logger.warning(
            "outbound_call_failed",
            status_code=resp.status_code,
            error=resp.text[:200],
            patient=to[-4:],
        )
        return False
    except Exception as exc:
        logger.error("outbound_call_error", error=str(exc), patient=to[-4:])
        return False


class OutboundCallService:
    """Places outbound appointment-related calls for a hospital via Plivo."""

    async def schedule_reminder(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        slot_time: datetime | None = None,
        hospital_id: str = "",
        tenant_slug: str = "default",
        appointment_date: str | None = None,
        appointment_time: str | None = None,
    ) -> bool:
        if slot_time is not None and _INDIA_TZ is not None:
            if slot_time.tzinfo is None:
                slot_local = _INDIA_TZ.localize(slot_time)
            else:
                slot_local = slot_time.astimezone(_INDIA_TZ)
            appointment_date = slot_local.strftime("%Y-%m-%d")
            appointment_time = slot_local.strftime("%H:%M")
        else:
            appointment_date = appointment_date or (
                slot_time.strftime("%Y-%m-%d") if slot_time else ""
            )
            appointment_time = appointment_time or (
                slot_time.strftime("%H:%M") if slot_time else ""
            )

        context = {
            "call_type": "reminder",
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "hospital_id": hospital_id,
        }
        answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        return await _place_call(patient_phone, answer_url, context, time_limit=120)

    async def schedule_callback_call(
        self,
        patient_phone: str,
        patient_name: str,
        reason: str,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        context = {
            "call_type": "callback",
            "patient_name": patient_name,
            "reason": reason,
            "hospital_id": hospital_id,
        }
        answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        return await _place_call(patient_phone, answer_url, context, time_limit=180)

    async def schedule_confirmation_call(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        slot_time: datetime | None,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        if slot_time is not None and _INDIA_TZ is not None:
            slot_local = (
                slot_time.astimezone(_INDIA_TZ)
                if slot_time.tzinfo
                else _INDIA_TZ.localize(slot_time)
            )
            appointment_date = slot_local.strftime("%d %B %Y")
            appointment_time = slot_local.strftime("%I:%M %p")
        else:
            appointment_date = ""
            appointment_time = ""

        context = {
            "call_type": "confirmation",
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "hospital_id": hospital_id,
        }
        answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        return await _place_call(patient_phone, answer_url, context, time_limit=180)

    async def schedule_followup_call(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        context = {
            "call_type": "followup",
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "hospital_id": hospital_id,
        }
        answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        return await _place_call(patient_phone, answer_url, context, time_limit=120)

    async def schedule_campaign_call(
        self,
        patient_phone: str,
        patient_name: str,
        campaign_type: str,
        campaign_message: str,
        hospital_id: str,
        campaign_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        context = {
            "call_type": "campaign",
            "campaign_type": campaign_type,
            "campaign_message": campaign_message[:200],
            "campaign_id": campaign_id,
            "patient_name": patient_name,
            "hospital_id": hospital_id,
        }
        answer_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        return await _place_call(patient_phone, answer_url, context, time_limit=120)

    async def get_pending_callbacks(self, db_pool) -> list[dict]:
        """Fetch pending callback requests (up to 10 at a time)."""
        query = """
            SELECT id, patient_phone, patient_name, reason, hospital_id, preferred_time
            FROM callbacks
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT 10
        """
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(query)
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.debug("get_pending_callbacks_skipped", reason=str(exc))
            return []

    async def get_pending_reminders(self, db_pool) -> list[dict]:
        """Query appointments in the next 24 hours where reminder_sent = False."""
        query = """
            SELECT
                a.id,
                a.patient_phone,
                a.patient_name,
                a.slot_time,
                a.hospital_id,
                d.name AS doctor_name
            FROM appointments a
            LEFT JOIN doctors d ON a.doctor_id = d.id
            WHERE
                a.reminder_sent = false
                AND a.status IN ('booked', 'confirmed')
                AND a.slot_time BETWEEN now() AND now() + interval '24 hours'
            ORDER BY a.slot_time
        """
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(query)
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.debug("get_pending_reminders_skipped", reason=str(exc))
            return []
