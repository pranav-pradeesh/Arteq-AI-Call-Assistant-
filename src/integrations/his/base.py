"""
Abstract HIS adapter interface.

Every HIS integration implements this. The agent and tools call
HISService.get_adapter(hospital_id) — if None, the local DB is used.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class HISAdapter(ABC):
    """Minimal contract every HIS adapter must satisfy."""

    @abstractmethod
    async def search_patient(self, phone: str) -> Optional[dict]:
        """
        Look up a patient by phone number.

        Returns dict with at minimum:
          his_patient_id: str
          name: str
          phone: str
        or None if not found.
        """

    @abstractmethod
    async def get_available_slots(self, doctor_id: str, date: str) -> list[str]:
        """
        Return available appointment slots for a doctor on a given date.

        Args:
            doctor_id: the HIS's doctor identifier (from his_doctor_id field)
            date: ISO date string "YYYY-MM-DD"

        Returns list of "HH:MM" strings (e.g. ["09:00", "09:30", "10:00"]).
        Returns [] if HIS is unavailable — caller falls back to local DB.
        """

    @abstractmethod
    async def create_appointment(
        self,
        his_patient_id: Optional[str],
        patient_name: str,
        patient_phone: str,
        his_doctor_id: str,
        appointment_date: str,
        appointment_time: str,
        notes: str = "",
    ) -> Optional[str]:
        """
        Book an appointment in the HIS.

        Returns the HIS appointment ID string, or None on failure.
        """

    @abstractmethod
    async def cancel_appointment(self, his_appointment_id: str) -> bool:
        """
        Cancel an appointment in the HIS.

        Returns True on success, False on failure.
        """

    @abstractmethod
    async def ping(self) -> bool:
        """Health check — True if HIS is reachable."""
