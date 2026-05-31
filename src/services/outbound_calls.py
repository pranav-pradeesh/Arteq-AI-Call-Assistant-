"""
Outbound Calls — proactive appointment reminder calls via Exotel.

Exotel's outbound call API dials the patient, then bridges the call
to our WebSocket handler where the AI (Arya) handles the reminder conversation.
"""
from __future__ import annotations

import json

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_CONNECT_URL = "https://api.exotel.in/v1/Accounts/{sid}/Calls/connect.json"


class OutboundCallService:
    """Schedules and manages outbound appointment reminder calls via Exotel."""

    async def schedule_reminder(
        self,
        patient_phone: str,
        patient_name: str,
        doctor_name: str,
        appointment_date: str,
        appointment_time: str,
        hospital_id: str,
        tenant_slug: str,
    ) -> bool:
        """
        Trigger an outbound reminder call to the patient via Exotel.

        Exotel dials the patient and, once answered, bridges the call to our
        inbound webhook URL where Arya handles the reminder conversation.

        Returns True on success, False on failure.
        """
        url = _CONNECT_URL.format(sid=settings.EXOTEL_SID)
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

    async def get_pending_reminders(self, db_pool) -> list[dict]:
        """
        Query appointments scheduled in the next 24 hours where reminder_sent = False.

        Returns a list of appointment dicts.
        If the appointments table does not exist yet, returns [] silently.
        """
        query = """
            SELECT
                id,
                patient_phone,
                patient_name,
                doctor_name,
                appointment_date::text,
                appointment_time::text,
                hospital_id,
                tenant_slug
            FROM appointments
            WHERE
                reminder_sent = FALSE
                AND appointment_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '1 day'
            ORDER BY appointment_date, appointment_time
        """
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(query)
            return [dict(row) for row in rows]
        except Exception as exc:
            # Table may not exist in MVP — fail silently
            logger.debug("get_pending_reminders_skipped", reason=str(exc))
            return []
