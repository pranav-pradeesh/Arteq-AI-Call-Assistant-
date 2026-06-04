"""
HL7 FHIR R4 HIS adapter.

Covers: Meditech, Epic, Cerner/Oracle Health, government ABDM/ABHA systems,
and any HIS that exposes a FHIR R4 REST API.

FHIR config shape (stored in hospitals.his_config):
{
  "enabled": true,
  "type": "fhir",
  "base_url": "https://fhir.their-his.com/fhir/R4",
  "auth": {
    "type": "bearer",
    "value": "access-token"
  },
  "practitioner_map": {
    "Dr. Ramesh Kumar": "Practitioner/12345"
  },
  "timeout_seconds": 8
}

FHIR resources used:
  Patient     — search by telecom (phone)
  Practitioner — map doctor name → FHIR ID
  Schedule/Slot — available appointment slots
  Appointment  — create / cancel
"""
from __future__ import annotations

from typing import Optional

import httpx

from src.integrations.his.base import HISAdapter
from src.observability.logger import get_logger

logger = get_logger(__name__)


class FHIRAdapter(HISAdapter):
    def __init__(self, config: dict) -> None:
        self._base = config["base_url"].rstrip("/")
        self._timeout = config.get("timeout_seconds", 8)
        self._practitioner_map: dict[str, str] = config.get("practitioner_map", {})
        auth = config.get("auth", {})
        token = auth.get("value", "")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
            "Content-Type": "application/fhir+json",
        }

    async def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self._base}/{path}", headers=self._headers, params=params or {})
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("fhir_get_failed", path=path, error=str(exc))
            return None

    async def _post(self, path: str, body: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(f"{self._base}/{path}", headers=self._headers, json=body)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("fhir_post_failed", path=path, error=str(exc))
            return None

    async def _patch(self, path: str, body: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.patch(f"{self._base}/{path}", headers=self._headers, json=body)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("fhir_patch_failed", path=path, error=str(exc))
            return None

    # ── FHIR helpers ─────────────────────────────────────────────────────────

    def _practitioner_id(self, doctor_name: str) -> Optional[str]:
        """Resolve doctor name to FHIR Practitioner ID from config map."""
        for k, v in self._practitioner_map.items():
            if doctor_name.lower() in k.lower() or k.lower() in doctor_name.lower():
                return v
        return None

    # ── HISAdapter interface ──────────────────────────────────────────────────

    async def search_patient(self, phone: str) -> Optional[dict]:
        # FHIR Patient search by phone number
        bundle = await self._get("Patient", {"telecom": phone})
        if not bundle or bundle.get("resourceType") != "Bundle":
            return None
        entries = bundle.get("entry", [])
        if not entries:
            return None
        patient = entries[0].get("resource", {})
        pid = patient.get("id", "")
        # Extract name
        names = patient.get("name", [{}])
        name_obj = names[0] if names else {}
        given = " ".join(name_obj.get("given", []))
        family = name_obj.get("family", "")
        return {
            "his_patient_id": f"Patient/{pid}" if pid else "",
            "name": f"{given} {family}".strip(),
            "phone": phone,
        }

    async def get_available_slots(self, doctor_id: str, date: str) -> list[str]:
        # FHIR Slot search: free slots for a practitioner on a date
        practitioner_ref = self._practitioner_id(doctor_id) or doctor_id
        bundle = await self._get("Slot", {
            "schedule.actor": practitioner_ref,
            "start": f"ge{date}T00:00:00",
            "end": f"le{date}T23:59:59",
            "status": "free",
        })
        if not bundle or bundle.get("resourceType") != "Bundle":
            return []
        slots = []
        for entry in bundle.get("entry", []):
            slot = entry.get("resource", {})
            start = slot.get("start", "")
            if "T" in start:
                slots.append(start.split("T")[1][:5])  # "HH:MM"
        return slots

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
        practitioner_ref = self._practitioner_id(his_doctor_id) or his_doctor_id
        resource = {
            "resourceType": "Appointment",
            "status": "booked",
            "start": f"{appointment_date}T{appointment_time}:00",
            "participant": [
                {
                    "actor": {"reference": practitioner_ref},
                    "status": "accepted",
                }
            ],
            "comment": notes,
        }
        if his_patient_id:
            resource["participant"].append({
                "actor": {"reference": his_patient_id},
                "status": "accepted",
            })
        resp = await self._post("Appointment", resource)
        if not resp:
            return None
        return f"Appointment/{resp.get('id', '')}" if resp.get("id") else None

    async def cancel_appointment(self, his_appointment_id: str) -> bool:
        # FHIR: PATCH Appointment/{id} with status=cancelled
        appt_id = his_appointment_id.replace("Appointment/", "")
        resp = await self._patch(f"Appointment/{appt_id}", {"status": "cancelled"})
        return resp is not None

    async def reschedule_appointment(
        self,
        his_appointment_id: str,
        appointment_date: str,
        appointment_time: str,
    ) -> bool:
        # FHIR: PATCH Appointment/{id} with a new start time
        appt_id = his_appointment_id.replace("Appointment/", "")
        resp = await self._patch(
            f"Appointment/{appt_id}",
            {"status": "booked", "start": f"{appointment_date}T{appointment_time}:00"},
        )
        return resp is not None

    async def ping(self) -> bool:
        # FHIR capability statement check
        data = await self._get("metadata")
        return data is not None and data.get("resourceType") == "CapabilityStatement"
