"""
SMS Service — patient notifications.

Note: Vobiz (our telephony provider) is a SIP trunk only — it does not
support SMS. All send methods are preserved for future carrier integration
but currently log a warning and return False.
"""
from __future__ import annotations

import urllib.parse

import structlog

logger = structlog.get_logger(__name__)


class SMSService:
    """Sends SMS messages to patients.

    Currently a no-op: Vobiz is SIP-only and does not provide SMS.
    Message methods are preserved so calling code needs no changes if a
    carrier is added later.
    """

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
        code: str = "",
    ) -> bool:
        """Send appointment confirmation details to patient.

        `code` is the booking reference; the token activates only after the
        consultation fee is paid at the hospital."""
        lines = [
            "Appointment Booked",
            f"Hospital: {hospital_name}",
            f"Doctor: Dr. {doctor_name}",
            f"Date: {date}",
            f"Time: {time}",
        ]
        if code:
            lines.append(f"Booking code: {code}")
            lines.append("Pay the fee at the hospital to activate your queue token.")
        lines.append("\nSent by Arya.")
        return await self._send(phone, "\n".join(lines))

    async def send_token_active(
        self,
        phone: str,
        hospital_name: str,
        patient_name: str,
        doctor_name: str,
        date: str,
        time: str,
        token_number: int,
    ) -> bool:
        """Notify the patient that payment is received and their queue token is live."""
        message = (
            "Payment Received — Token Active\n"
            f"Hospital: {hospital_name}\n"
            f"Patient: {patient_name}\n"
            f"Doctor: Dr. {doctor_name}\n"
            f"Date: {date}\n"
            f"Time: {time}\n"
            f"Your token number: {token_number}\n\n"
            "Show this at the desk. Sent by Arya."
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

    async def send_appointment_reminder(
        self,
        phone: str,
        hospital_name: str,
        patient_name: str,
        doctor_name: str,
        date: str,
        time: str,
    ) -> bool:
        """Send appointment reminder (day before or same day)."""
        message = (
            f"Reminder — {hospital_name}\n"
            f"Patient: {patient_name}\n"
            f"Doctor: Dr. {doctor_name}\n"
            f"Date: {date}\n"
            f"Time: {time}\n"
            "Please arrive 10 minutes early. Arya."
        )
        return await self._send(phone, message)

    async def send_doctor_availability(
        self,
        phone: str,
        hospital_name: str,
        patient_name: str,
        doctor_name: str,
        date: str,
        status: str,
    ) -> bool:
        """Notify patient of doctor availability status on appointment day.

        status: 'available' | 'delayed' | 'unavailable'
        """
        _STATUS_MSG = {
            "available":   f"Dr. {doctor_name} is available and ready to see you today.",
            "delayed":     f"Dr. {doctor_name} is running slightly delayed today. Please wait at the OPD.",
            "unavailable": f"Dr. {doctor_name} is unfortunately unavailable today. Please contact {hospital_name} to reschedule.",
        }
        body = _STATUS_MSG.get(status, f"Doctor availability update from {hospital_name}.")
        message = (
            f"{hospital_name}\n"
            f"Patient: {patient_name}\n"
            f"{body}\n"
            "Arya."
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
        """SMS is not available — Vobiz is a SIP trunk only."""
        logger.warning("sms_skipped_no_sms_provider", phone=phone[:6] + "****")
        return False
