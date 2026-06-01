"""
SMS Service — sends SMS to patients via Exotel.

Used for:
- Hospital location / Google Maps link
- Appointment confirmation details
- Lab schedule / test prep instructions
- Doctor schedule details
"""
from __future__ import annotations

import urllib.parse

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class SMSService:
    """Sends SMS messages to patients via Exotel SMS API."""

    _SMS_URL = "https://api.exotel.in/v1/Accounts/{sid}/Sms/send.json"

    async def send_maps_link(
        self,
        phone: str,
        hospital_name: str,
        address: str,
    ) -> bool:
        """Send hospital location with Google Maps link."""
        maps_url = f"https://maps.google.com/?q={urllib.parse.quote(address)}"
        message = (
            f"{hospital_name}\n"
            f"Address: {address}\n"
            f"Map: {maps_url}\n\n"
            "Sent by Arya, your hospital assistant."
        )
        return await self._send(phone, message)

    async def send_appointment_confirmation(
        self,
        phone: str,
        hospital_name: str,
        patient_name: str,
        doctor_name: str,
        date: str,
        time: str,
    ) -> bool:
        """Send appointment confirmation details to patient."""
        message = (
            f"Appointment Confirmed\n"
            f"Hospital: {hospital_name}\n"
            f"Doctor: Dr. {doctor_name}\n"
            f"Date: {date}\n"
            f"Time: {time}\n\n"
            "Sent by Arya."
        )
        return await self._send(phone, message)

    async def send_lab_schedule(
        self,
        phone: str,
        hospital_name: str,
        test_name: str,
        instructions: str,
        lab_timing: str,
    ) -> bool:
        """Send lab test schedule and preparation instructions."""
        message = (
            f"Lab Test: {test_name}\n"
            f"Timing: {lab_timing}\n"
            f"Instructions: {instructions}\n\n"
            f"{hospital_name}"
        )
        return await self._send(phone, message)

    async def send_appointment_cancellation(
        self,
        phone: str,
        hospital_name: str,
        patient_name: str,
        doctor_name: str,
        date: str,
    ) -> bool:
        """Notify patient that their appointment has been cancelled."""
        message = (
            f"Appointment Cancelled\n"
            f"Hospital: {hospital_name}\n"
            f"Patient: {patient_name}\n"
            f"Doctor: Dr. {doctor_name}\n"
            f"Date: {date}\n"
            "Contact us to rebook. Arya."
        )
        return await self._send(phone, message)

    async def send_callback_confirmation(
        self,
        phone: str,
        hospital_name: str,
        preferred_time: str,
    ) -> bool:
        """Confirm a callback request was registered."""
        message = (
            f"Callback Registered — {hospital_name}\n"
            f"We will call you back {preferred_time}.\n"
            "Arya, your hospital assistant."
        )
        return await self._send(phone, message)

    async def send_call_summary(
        self,
        phone: str,
        hospital_name: str,
        summary: str,
    ) -> bool:
        """Send a brief call summary after the call ends."""
        message = (
            f"{hospital_name}\n"
            f"Call summary: {summary[:140]}\n"
            "Arya"
        )
        return await self._send(phone, message)

    async def send_custom(self, phone: str, message: str) -> bool:
        """Send a custom message to the given phone number."""
        return await self._send(phone, message)

    async def _send(self, phone: str, message: str) -> bool:
        """Core send logic — POST to Exotel SMS API."""
        url = self._SMS_URL.format(sid=settings.EXOTEL_SID)
        payload = {
            "From": settings.EXOTEL_CALLER_ID,
            "To": phone,
            "Body": message,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    data=payload,
                    auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                )
            if response.status_code == 200:
                logger.info(
                    "sms_sent",
                    phone=phone[:6] + "****",
                    msg_len=len(message),
                )
                return True
            logger.warning(
                "sms_failed",
                phone=phone[:6] + "****",
                status_code=response.status_code,
                error=response.text[:200],
            )
            return False
        except Exception as exc:
            logger.error("sms_failed", error=str(exc), phone=phone[:6] + "****")
            return False
