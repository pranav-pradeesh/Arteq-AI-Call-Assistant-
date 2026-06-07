"""
Generic REST HIS adapter.

Works with any REST API by applying URL templates and a field name mapping
defined in the per-hospital his_config JSONB.

This single adapter is designed to satisfy almost any REST-based HIS by config
alone (auth scheme, URL templates, and a field/path map) — no code per vendor.

Config shape (stored in hospitals.his_config):
{
  "enabled": true,
  "type": "generic_rest",   # or "auto" — service picks fhir vs generic
  "base_url": "https://api.their-his.com/v1",
  "auth": {
    # type: bearer | api_key | basic | oauth2
    "type": "oauth2",
    "value": "token-or-key",          # bearer/api_key/basic
    "header": "X-Api-Key",            # api_key only
    "headers": {"X-Facility-Id": "42"},  # optional static headers sent on every call
    # oauth2 client-credentials (enterprise / ABDM-gateway style):
    "token_url": "https://auth.their-his.com/oauth/token",
    "client_id": "...", "client_secret": "...",
    "grant_type": "client_credentials", "scope": "appointments"
  },
  "endpoints": {
    "search_patient":       "GET /patients?phone={phone}",
    "get_slots":            "GET /doctors/{doctor_id}/slots?date={date}",
    "create_appointment":   "POST /appointments",
    "cancel_appointment":   "POST /appointments/{appointment_id}/cancel",
    "reschedule_appointment": "POST /appointments/{appointment_id}/reschedule",
    "health":               "GET /health"   # optional, used by ping()
  },
  "field_map": {
    # values may be dotted paths to read inside envelopes, e.g. "data.id"
    "his_patient_id":    "id",
    "his_doctor_id":     "consultant_id",
    "patient_name":      "full_name",
    "patient_phone":     "mobile",
    "appointment_date":  "visit_date",
    "appointment_time":  "visit_time",
    "appointment_id":    "appointment_id",
    "patient_root":      "data.results",  # optional: unwrap search response
    "slots_list_key":    "data.slots",    # where the slot list lives (dotted ok)
    "slot_time_field":   "start"          # if slots are objects, the time field
  },
  "timeout_seconds": 5
}
"""
from __future__ import annotations

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
        # OAuth2 client-credentials token cache (used only by auth.type == "oauth2")
        self._token: str = ""
        self._token_exp: float = 0.0

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _oauth_token(self, auth: dict) -> str:
        """Fetch (and cache) an OAuth2 client-credentials access token.

        Covers enterprise / ABDM-gateway style HIS that issue short-lived bearer
        tokens from a token endpoint. The token is reused until ~30s before it
        expires, then refreshed.
        """
        import time
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        token_url = auth.get("token_url", "")
        if not token_url:
            return ""
        data = {
            "grant_type": auth.get("grant_type", "client_credentials"),
            "client_id": auth.get("client_id", ""),
            "client_secret": auth.get("client_secret", ""),
        }
        if auth.get("scope"):
            data["scope"] = auth["scope"]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(token_url, data=data)
                r.raise_for_status()
                j = r.json()
            self._token = str(j.get("access_token", ""))
            self._token_exp = time.time() + float(j.get("expires_in", 3600))
            return self._token
        except Exception as exc:
            logger.warning("his_oauth_failed", url=token_url, error=str(exc))
            return ""

    async def _headers(self) -> dict:
        auth = self._cfg.get("auth", {})
        auth_type = auth.get("type", "")
        val = auth.get("value", "")
        # Base headers + any static custom headers the HIS requires (e.g. a tenant
        # or facility id). Lets one generic adapter satisfy almost any REST HIS.
        headers = {"Accept": "application/json"}
        headers.update(auth.get("headers", {}) or {})
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {val}"
        elif auth_type == "api_key":
            headers[auth.get("header", "X-Api-Key")] = val
        elif auth_type == "basic":
            import base64
            headers["Authorization"] = "Basic " + base64.b64encode(val.encode()).decode()
        elif auth_type == "oauth2":
            token = await self._oauth_token(auth)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _fmap(self, our_key: str) -> str:
        """Map our field name to the HIS field name (default: same name)."""
        return self._fm.get(our_key, our_key)

    @staticmethod
    def _dig(obj, path: str):
        """Read a possibly-nested value by dotted path (e.g. "data.patient.id").

        Handles HIS responses that wrap payloads in envelopes. At each step, if
        the current node is a list, the first element is used. Returns None if any
        step is missing.
        """
        cur = obj
        for part in str(path).split("."):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur

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
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.warning("his_get_failed", url=url, error=str(exc))
            return None

    async def _post(self, url: str, body: dict) -> Optional[dict]:
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(url, json=body, headers=headers)
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
        # Unwrap an envelope if configured (e.g. field_map.patient_root = "data.results").
        root = self._fm.get("patient_root")
        if root:
            data = self._dig(data, root)
        # If response (or unwrapped node) is a list, take the first item.
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            return None
        return {
            "his_patient_id": str(self._dig(row, self._fmap("his_patient_id")) or ""),
            "name": self._dig(row, self._fmap("patient_name")) or "",
            "phone": self._dig(row, self._fmap("patient_phone")) or phone,
        }

    async def get_available_slots(self, doctor_id: str, date: str) -> list[str]:
        _, url = self._parse_endpoint("get_slots", doctor_id=doctor_id, date=date)
        if not url:
            return []
        data = await self._get(url)
        if not data:
            return []
        # Support a bare list, {"slots": [...]}, or a nested path via field_map
        # (e.g. slots_list_key = "data.availableSlots").
        if isinstance(data, dict):
            slots_key = self._fmap("slots_list_key")
            data = self._dig(data, slots_key) or data.get("slots", [])
        if isinstance(data, list):
            # Slots may be plain "HH:MM" strings or objects — pull the time field.
            time_key = self._fm.get("slot_time_field")
            out = []
            for s in data:
                val = None
                if isinstance(s, dict):
                    val = self._dig(s, time_key) if time_key else (s.get("time") or s.get("start"))
                elif s:
                    val = s
                if not val:
                    continue
                sval = str(val)
                # Normalise an ISO datetime ("...T09:30:00") down to "HH:MM".
                out.append(sval.split("T", 1)[1][:5] if "T" in sval else sval)
            return out
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
        appt_id = self._dig(resp, appt_id_key)
        if appt_id is None:
            appt_id = resp.get("id", "") if isinstance(resp, dict) else ""
        return str(appt_id) or None

    async def cancel_appointment(self, his_appointment_id: str) -> bool:
        _, url = self._parse_endpoint("cancel_appointment", appointment_id=his_appointment_id)
        if not url:
            return False
        resp = await self._post(url, {"appointment_id": his_appointment_id})
        return resp is not None

    async def reschedule_appointment(
        self,
        his_appointment_id: str,
        appointment_date: str,
        appointment_time: str,
    ) -> bool:
        # Optional endpoint: "reschedule_appointment":
        #   "POST /appointments/{appointment_id}/reschedule"
        _, url = self._parse_endpoint("reschedule_appointment", appointment_id=his_appointment_id)
        if not url:
            return False
        body = {
            self._fmap("appointment_id"): his_appointment_id,
            self._fmap("appointment_date"): appointment_date,
            self._fmap("appointment_time"): appointment_time,
        }
        resp = await self._post(url, body)
        return resp is not None

    async def ping(self) -> bool:
        try:
            headers = await self._headers()
            # Prefer an explicit health endpoint if configured, else hit the base.
            _, health_url = self._parse_endpoint("health")
            url = health_url or self._base_url
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(url, headers=headers)
                return r.status_code < 500
        except Exception:
            return False
