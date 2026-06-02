"""
Generic REST HIS adapter.

Works with any REST API by applying URL templates and a field name mapping
defined in the per-hospital his_config JSONB.

Config shape (stored in hospitals.his_config):
{
  "enabled": true,
  "type": "generic_rest",
  "base_url": "https://api.their-his.com/v1",
  "auth": {
    "type": "bearer" | "api_key" | "basic",
    "value": "token-or-key",
    "header": "X-Api-Key"   # only for api_key type
  },
  "endpoints": {
    "search_patient":       "GET /patients?phone={phone}",
    "get_slots":            "GET /doctors/{doctor_id}/slots?date={date}",
    "create_appointment":   "POST /appointments",
    "cancel_appointment":   "POST /appointments/{appointment_id}/cancel"
  },
  "field_map": {
    "his_patient_id":    "id",
    "his_doctor_id":     "consultant_id",
    "patient_name":      "full_name",
    "patient_phone":     "mobile",
    "appointment_date":  "visit_date",
    "appointment_time":  "visit_time",
    "appointment_id":    "appointment_id",
    "slots_list_key":    "slots"   # key in GET /slots response that holds the list
  },
  "timeout_seconds": 5
}
"""
from __future__ import annotations

import sys
from typing import Optional

import httpx

from src.integrations.his.base import HISAdapter
from src.observability.logger import get_logger

logger = get_logger(__name__)


class GenericRestAdapter(HISAdapter):
    def __init__(self, config: dict) -> None:
        self._cfg = config
        self._base_url = config["base_url"].rstrip("/")
        self._timeout = config.get("timeout_seconds", 5)
        self._endpoints = config.get("endpoints", {})
        self._fm = config.get("field_map", {})  # our name → their name

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        auth = self._cfg.get("auth", {})
        auth_type = auth.get("type", "")
        val = auth.get("value", "")
        if auth_type == "bearer":
            return {"Authorization": f"Bearer {val}", "Accept": "application/json"}
        if auth_type == "api_key":
            header = auth.get("header", "X-Api-Key")
            return {header: val, "Accept": "application/json"}
        if auth_type == "basic":
            import base64
            encoded = base64.b64encode(val.encode()).decode()
            return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}
        return {"Accept": "application/json"}

    def _fmap(self, our_key: str) -> str:
        """Map our field name to the HIS field name (default: same name)."""
        return self._fm.get(our_key, our_key)

    def _parse_endpoint(self, key: str, **subs) -> tuple[str, str]:
        """
        Parse endpoint spec like "GET /patients?phone={phone}".
        Returns (method, full_url).
        """
        spec = self._endpoints.get(key, "")
        if not spec:
            return "", ""
        parts = spec.split(" ", 1)
        method = parts[0].upper() if len(parts) == 2 else "GET"
        path = parts[-1]
        for k, v in subs.items():
            path = path.replace(f"{{{k}}}", str(v))
        return method, f"{self._base_url}{path}"

    async def _get(self, url: str) -> Optional[dict | list]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(url, headers=self._headers())
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("his_get_failed", url=url, error=str(exc))
            return None

    async def _post(self, url: str, body: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(url, json=body, headers=self._headers())
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("his_post_failed", url=url, error=str(exc))
            return None

    # ── HISAdapter interface ──────────────────────────────────────────────────

    async def search_patient(self, phone: str) -> Optional[dict]:
        _, url = self._parse_endpoint("search_patient", phone=phone)
        if not url:
            return None
        data = await self._get(url)
        if not data:
            return None
        # If response is a list, take the first item
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            return None
        return {
            "his_patient_id": str(row.get(self._fmap("his_patient_id"), "")),
            "name": row.get(self._fmap("patient_name"), ""),
            "phone": row.get(self._fmap("patient_phone"), phone),
        }

    async def get_available_slots(self, doctor_id: str, date: str) -> list[str]:
        _, url = self._parse_endpoint("get_slots", doctor_id=doctor_id, date=date)
        if not url:
            return []
        data = await self._get(url)
        if not data:
            return []
        # Support {"slots": [...]} or a bare list
        slots_key = self._fmap("slots_list_key")
        if isinstance(data, dict):
            data = data.get(slots_key, data.get("slots", []))
        if isinstance(data, list):
            return [str(s) for s in data if s]
        return []

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
        _, url = self._parse_endpoint("create_appointment")
        if not url:
            return None
        body = {
            self._fmap("his_patient_id"): his_patient_id or "",
            self._fmap("patient_name"): patient_name,
            self._fmap("patient_phone"): patient_phone,
            self._fmap("his_doctor_id"): his_doctor_id,
            self._fmap("appointment_date"): appointment_date,
            self._fmap("appointment_time"): appointment_time,
            "notes": notes,
        }
        resp = await self._post(url, body)
        if not resp:
            return None
        appt_id_key = self._fmap("appointment_id")
        return str(resp.get(appt_id_key, resp.get("id", ""))) or None

    async def cancel_appointment(self, his_appointment_id: str) -> bool:
        _, url = self._parse_endpoint("cancel_appointment", appointment_id=his_appointment_id)
        if not url:
            return False
        resp = await self._post(url, {"appointment_id": his_appointment_id})
        return resp is not None

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(self._base_url, headers=self._headers())
                return r.status_code < 500
        except Exception:
            return False
