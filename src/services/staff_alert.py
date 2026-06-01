"""
Staff Alert Service — real-time SMS to the duty manager.

Fires on key call events so hospital staff are never in the dark:
  - New appointment booked by Arya
  - Appointment cancelled
  - Emergency detected on a call
  - Unresolved question (missed by Arya → needs KB update)
  - Post-call summary for complex interactions

Configure STAFF_ALERT_PHONE in settings (duty manager's mobile).
Leave blank to disable all alerts silently.
"""
from __future__ import annotations

from src.config.settings import settings
from src.observability.logger import get_logger
from src.services.sms_service import SMSService

logger = get_logger(__name__)


class StaffAlertService:
    """Fire-and-forget SMS alerts to the duty manager."""

    def __init__(self) -> None:
        self._sms = SMSService()
        self._phone: str = getattr(settings, "STAFF_ALERT_PHONE", "")

    def _enabled(self) -> bool:
        return bool(self._phone)

    async def alert_new_booking(
        self,
        patient_name: str,
        patient_phone: str,
        doctor_name: str,
        date: str,
        time: str,
        call_id: str = "",
    ) -> None:
        if not self._enabled() or not getattr(settings, "STAFF_ALERT_ON_BOOKING", True):
            return
        msg = (
            f"[Arya] New appointment booked\n"
            f"Patient: {patient_name} ({patient_phone[-4:].rjust(10, '*')})\n"
            f"Doctor: Dr. {doctor_name}\n"
            f"Slot: {date} {time}\n"
            f"Ref: {call_id[:8]}"
        )
        await self._send(msg)

    async def alert_cancellation(
        self,
        patient_name: str,
        patient_phone: str,
        doctor_name: str,
        date: str,
        call_id: str = "",
    ) -> None:
        if not self._enabled() or not getattr(settings, "STAFF_ALERT_ON_CANCEL", True):
            return
        msg = (
            f"[Arya] Appointment CANCELLED\n"
            f"Patient: {patient_name} ({patient_phone[-4:].rjust(10, '*')})\n"
            f"Doctor: Dr. {doctor_name} | Date: {date}\n"
            f"Ref: {call_id[:8]}"
        )
        await self._send(msg)

    async def alert_emergency(
        self,
        patient_phone: str,
        transcript_snippet: str,
        call_id: str = "",
    ) -> None:
        if not self._enabled() or not getattr(settings, "STAFF_ALERT_ON_EMERGENCY", True):
            return
        msg = (
            f"[Arya] EMERGENCY CALL\n"
            f"Phone: {patient_phone[-4:].rjust(10, '*')}\n"
            f"Snippet: {transcript_snippet[:80]}\n"
            f"-> Transferred to emergency\n"
            f"Ref: {call_id[:8]}"
        )
        await self._send(msg)

    async def alert_missed_question(
        self,
        question: str,
        language: str,
        call_id: str = "",
    ) -> None:
        if not self._enabled():
            return
        msg = (
            f"[Arya] Unanswered question (add to KB?)\n"
            f"Q: {question[:120]}\n"
            f"Lang: {language} | Ref: {call_id[:8]}"
        )
        await self._send(msg)

    async def alert_call_summary(
        self,
        patient_phone: str,
        turns: int,
        outcome: str,
        summary: str,
        call_id: str = "",
    ) -> None:
        if not self._enabled():
            return
        msg = (
            f"[Arya] Call summary\n"
            f"Phone: {patient_phone[-4:].rjust(10, '*')} | {turns} turns | {outcome}\n"
            f"{summary[:120]}\n"
            f"Ref: {call_id[:8]}"
        )
        await self._send(msg)

    async def _send(self, message: str) -> None:
        try:
            await self._sms.send_custom(phone=self._phone, message=message)
        except Exception as exc:
            logger.warning("staff_alert_failed", error=str(exc))
