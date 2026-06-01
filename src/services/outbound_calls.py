"""
Outbound Calls — proactive appointment reminder calls via Exotel.

Exotel's outbound call API dials the patient, then bridges the call
to our WebSocket handler where the AI (Arya) handles the reminder conversation.
"""
from __future__ import annotations

import json
from datetime import datetime

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

def _connect_url(sid: str) -> str:
    return f"https://{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{sid}/Calls/connect.json"

try:
    import pytz as _pytz
    _INDIA_TZ = _pytz.timezone("Asia/Kolkata")
except ImportError:
    _INDIA_TZ = None  # type: ignore[assignment]


class OutboundCallService:
    """Schedules and manages outbound appointment reminder calls via Exotel."""

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
        """
        Trigger an outbound reminder call to the patient via Exotel.

        Exotel dials the patient and, once answered, bridges the call to our
        inbound webhook URL where Arya handles the reminder conversation.

        Prefer passing ``slot_time`` (a timestamp, timezone-aware preferred):
        the human-readable date/time strings are derived in Asia/Kolkata.
        For backward compatibility, ``appointment_date``/``appointment_time``
        strings may be passed directly instead.

        Returns True on success, False on failure.
        """
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

        url = _connect_url(settings.EXOTEL_SID)
        webhook_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        status_callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/status"
        custom_field = json.dumps(
            {
                "call_type": "reminder",
                "patient_name": patient_name,
                "doctor_name": doctor_name,
                "appointment_date": appointment_date,
                "appointment_time": appointment_time,
                "hospital_id": hospital_id,
            }
        )

        payload = {
            "From": patient_phone,
            "To": settings.EXOTEL_CALLER_ID,
            "CallerId": settings.EXOTEL_CALLER_ID,
            "Url": webhook_url,
            "CustomField": custom_field,
            "TimeLimit": "120",
            "StatusCallback": status_callback_url,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code in (200, 201):
                logger.info(
                    "outbound_reminder_scheduled",
                    patient=patient_phone[-4:],
                    doctor=doctor_name,
                    date=appointment_date,
                )
                return True
            logger.warning(
                "outbound_reminder_failed",
                status_code=response.status_code,
                error=response.text[:200],
                patient=patient_phone[-4:],
            )
            return False
        except Exception as exc:
            logger.error(
                "outbound_reminder_failed",
                error=str(exc),
                patient=patient_phone[-4:],
            )
            return False

    async def schedule_callback_call(
        self,
        patient_phone: str,
        patient_name: str,
        reason: str,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        """Trigger an outbound callback call via Exotel."""
        url = _connect_url(settings.EXOTEL_SID)
        webhook_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        status_callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/status"
        custom_field = json.dumps({
            "call_type": "callback",
            "patient_name": patient_name,
            "reason": reason,
            "hospital_id": hospital_id,
        })
        payload = {
            "From": patient_phone,
            "To": settings.EXOTEL_CALLER_ID,
            "CallerId": settings.EXOTEL_CALLER_ID,
            "Url": webhook_url,
            "CustomField": custom_field,
            "TimeLimit": "180",
            "StatusCallback": status_callback_url,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code in (200, 201):
                logger.info("outbound_callback_scheduled", patient=patient_phone[-4:])
                return True
            logger.warning("outbound_callback_failed",
                           status_code=response.status_code, error=response.text[:200])
            return False
        except Exception as exc:
            logger.error("outbound_callback_failed", error=str(exc))
            return False

    async def schedule_confirmation_call(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        slot_time: datetime | None,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        """Trigger an outbound advance confirmation call 1–2 weeks before appointment."""
        if slot_time is not None and _INDIA_TZ is not None:
            slot_local = slot_time.astimezone(_INDIA_TZ) if slot_time.tzinfo else _INDIA_TZ.localize(slot_time)
            appointment_date = slot_local.strftime("%d %B %Y")   # human-readable for Arya
            appointment_time = slot_local.strftime("%I:%M %p")
        else:
            appointment_date = ""
            appointment_time = ""

        url = _connect_url(settings.EXOTEL_SID)
        webhook_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        custom_field = json.dumps({
            "call_type": "confirmation",
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "hospital_id": hospital_id,
        })
        payload = {
            "From": patient_phone,
            "To": settings.EXOTEL_CALLER_ID,
            "CallerId": settings.EXOTEL_CALLER_ID,
            "Url": webhook_url,
            "CustomField": custom_field,
            "TimeLimit": "180",
            "StatusCallback": f"{settings.PUBLIC_BASE_URL}/api/v1/call/status",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code in (200, 201):
                logger.info("outbound_confirmation_scheduled",
                            patient=patient_phone[-4:], doctor=doctor_name, date=appointment_date)
                return True
            logger.warning("outbound_confirmation_failed",
                           status_code=response.status_code, error=response.text[:200])
            return False
        except Exception as exc:
            logger.error("outbound_confirmation_failed", error=str(exc))
            return False

    async def schedule_followup_call(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        hospital_id: str,
        tenant_slug: str = "default",
    ) -> bool:
        """Call patient 3 days after appointment to check on their well-being."""
        url = _connect_url(settings.EXOTEL_SID)
        webhook_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        custom_field = json.dumps({
            "call_type": "followup",
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "hospital_id": hospital_id,
        })
        payload = {
            "From": patient_phone,
            "To": settings.EXOTEL_CALLER_ID,
            "CallerId": settings.EXOTEL_CALLER_ID,
            "Url": webhook_url,
            "CustomField": custom_field,
            "TimeLimit": "120",
            "StatusCallback": f"{settings.PUBLIC_BASE_URL}/api/v1/call/status",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code in (200, 201):
                logger.info("followup_call_scheduled", patient=patient_phone[-4:])
                return True
            return False
        except Exception as exc:
            logger.error("followup_call_failed", error=str(exc))
            return False

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
        """Place an outbound health campaign call."""
        url = _connect_url(settings.EXOTEL_SID)
        webhook_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        custom_field = json.dumps({
            "call_type": "campaign",
            "campaign_type": campaign_type,
            "campaign_message": campaign_message[:200],
            "campaign_id": campaign_id,
            "patient_name": patient_name,
            "hospital_id": hospital_id,
        })
        payload = {
            "From": patient_phone,
            "To": settings.EXOTEL_CALLER_ID,
            "CallerId": settings.EXOTEL_CALLER_ID,
            "Url": webhook_url,
            "CustomField": custom_field,
            "TimeLimit": "120",
            "StatusCallback": f"{settings.PUBLIC_BASE_URL}/api/v1/call/status",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code in (200, 201):
                logger.info("campaign_call_placed", patient=patient_phone[-4:],
                            campaign_id=campaign_id[:8])
                return True
            return False
        except Exception as exc:
            logger.error("campaign_call_failed", error=str(exc))
            return False

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
        """
        Query appointments scheduled in the next 24 hours where reminder_sent = False.

        Returns a list of appointment dicts.
        If the appointments table does not exist yet, returns [] silently.
        """
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
            # Table may not exist in MVP — fail silently
            logger.debug("get_pending_reminders_skipped", reason=str(exc))
            return []
