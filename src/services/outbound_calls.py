"""
Outbound Calls — proactive reminder, confirmation, callback, followup,
and campaign calls via LiveKit SIP.

LiveKit dials the patient via the configured SIP carrier's outbound trunk and
creates a room with the call context in room metadata. The agent worker
auto-dispatches to the room and reads the context to tailor its opening/script.

Carrier is selected by TELEPHONY_CARRIER (default "vobiz", the current carrier):
  • vobiz → src.services.vobiz_sip.dial_outbound_vobiz
            (run POST /admin/sip/vobiz/setup once → LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID)
  • plivo → src.services.livekit_sip.dial_outbound (legacy)
            (run POST /admin/sip/setup once → LIVEKIT_SIP_OUTBOUND_TRUNK_ID)
"""
from __future__ import annotations

import os
from datetime import datetime

import structlog


logger = structlog.get_logger(__name__)

try:
    import pytz as _pytz
    _INDIA_TZ = _pytz.timezone("Asia/Kolkata")
except ImportError:
    _INDIA_TZ = None  # type: ignore[assignment]


async def _dial(
    patient_phone: str,
    context: dict,
    tenant_slug: str = "default",
) -> bool:
    """Dial patient via the configured SIP carrier. Returns True on success.

    Carrier selected by TELEPHONY_CARRIER (default "vobiz" — the current sole
    carrier). The legacy Plivo path stays reachable with TELEPHONY_CARRIER=plivo.
    """
    carrier = (os.getenv("TELEPHONY_CARRIER", "vobiz") or "vobiz").strip().lower()
    try:
        if carrier == "vobiz":
            from src.services.vobiz_sip import dial_outbound_vobiz
            room = await dial_outbound_vobiz(patient_phone, tenant_slug, context)
        else:
            from src.services.livekit_sip import dial_outbound
            room = await dial_outbound(patient_phone, tenant_slug, context)
        return bool(room)
    except Exception as exc:
        logger.error(
            "outbound_dial_error", error=str(exc), carrier=carrier,
            patient=patient_phone[-4:],
        )
        return False


class OutboundCallService:
    """Places outbound appointment-related calls for a hospital via LiveKit SIP."""

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
            slot_local = (
                slot_time.astimezone(_INDIA_TZ)
                if slot_time.tzinfo
                else _INDIA_TZ.localize(slot_time)
            )
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
        return await _dial(patient_phone, context, tenant_slug)

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
        return await _dial(patient_phone, context, tenant_slug)

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
        return await _dial(patient_phone, context, tenant_slug)

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
        return await _dial(patient_phone, context, tenant_slug)

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
        return await _dial(patient_phone, context, tenant_slug)

    async def get_pending_callbacks(self, db_pool) -> list[dict]:
        query = """
            SELECT c.id, c.patient_phone, c.patient_name, c.reason,
                   c.hospital_id, c.preferred_time, h.slug AS slug
            FROM callbacks c
            LEFT JOIN hospitals h ON h.id = c.hospital_id
            WHERE c.status = 'pending'
            ORDER BY c.created_at
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
        query = """
            SELECT
                a.id,
                a.patient_phone,
                a.patient_name,
                a.slot_time,
                a.hospital_id,
                d.name AS doctor_name,
                h.slug  AS slug
            FROM appointments a
            LEFT JOIN doctors d ON a.doctor_id = d.id
            LEFT JOIN hospitals h ON h.id = a.hospital_id
            WHERE
                a.reminder_sent = false
                AND a.reminder_attempts < 3
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
