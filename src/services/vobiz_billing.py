"""
Vobiz CDR cost reconciliation.

Vobiz is the one telephony provider that exposes the REAL billed cost per call
(its CDR records carry "cost in INR"). This module pulls recent CDRs and writes
the real cost onto the matching call_logs row, replacing the duration-based
telephony estimate the agent wrote at call end.

It is deliberately defensive and opt-in (settings.VOBIZ_CDR_ENABLED, default
False): the exact CDR endpoint path and auth scheme must be confirmed against the
Vobiz console/docs, and field names are read by trying several aliases, so a
schema we didn't anticipate degrades to "no match" (the estimate stays) rather
than a crash or a wrong number.

Matching: an outbound call_log stores the patient's number in `caller`; we match
it to a CDR's recipient (last 10 digits) whose start time is within
VOBIZ_CDR_MATCH_WINDOW_SECONDS of the call_log's start.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


def _last10(phone: str | None) -> str:
    """Last 10 digits of a phone number, for carrier-format-agnostic matching."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:]


def _first(d: dict, *keys: str) -> Any:
    """Return the first present, non-None value among several possible key names."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _cost_to_paise(val: Any) -> Optional[int]:
    """Parse a CDR cost field (INR, possibly a string like '0.70' or '₹0.70')."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return max(0, round(float(val) * 100))
    s = "".join(ch for ch in str(val) if ch.isdigit() or ch in ".-")
    try:
        return max(0, round(float(s) * 100))
    except ValueError:
        return None


def _parse_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _auth_headers() -> dict:
    """Best-effort auth — send the API key several common ways; Vobiz ignores the
    headers it doesn't use. Confirm the real scheme against the Vobiz docs."""
    key = settings.VOBIZ_API_KEY or ""
    secret = settings.VOBIZ_API_SECRET or ""
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["x-api-key"] = key
    if secret:
        headers["x-api-secret"] = secret
    return headers


async def fetch_recent_cdrs(limit: Optional[int] = None) -> list[dict]:
    """Fetch recent CDR records from the Vobiz API. Returns [] on any failure."""
    base = (settings.VOBIZ_API_BASE or "").rstrip("/")
    path = settings.VOBIZ_CDR_RECENT_PATH or "/v1/cdr/recent"
    url = f"{base}{path}"
    params = {"limit": int(limit or settings.VOBIZ_CDR_RECENT_LIMIT)}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params, headers=_auth_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("vobiz_cdr_fetch_failed", url=url, error=str(exc))
        return []
    # Accept either a bare list or {data|results|cdrs: [...]}.
    if isinstance(data, dict):
        for k in ("data", "results", "cdrs", "records", "items"):
            if isinstance(data.get(k), list):
                return data[k]
        return []
    return data if isinstance(data, list) else []


def _cdr_recipient(cdr: dict) -> str:
    return _last10(_first(cdr, "to", "recipient", "called_number", "callee",
                          "destination", "dst", "b_number"))


def _cdr_cost_paise(cdr: dict) -> Optional[int]:
    return _cost_to_paise(_first(cdr, "cost", "cost_inr", "price", "amount",
                                 "charge", "rate"))


def _cdr_start(cdr: dict) -> Optional[datetime]:
    return _parse_dt(_first(cdr, "start_time", "started_at", "start",
                            "answer_time", "created_at", "initiated_at"))


async def reconcile_cdr_costs(pool) -> int:
    """Match un-reconciled outbound calls to Vobiz CDRs and write the real cost.

    Returns the number of call_logs rows reconciled this pass.
    """
    if not settings.VOBIZ_CDR_ENABLED:
        return 0

    cdrs = await fetch_recent_cdrs()
    if not cdrs:
        return 0

    # Index CDRs by recipient last-10 for an O(1) lookup per call.
    by_recipient: dict[str, list[dict]] = {}
    for c in cdrs:
        rcpt = _cdr_recipient(c)
        if rcpt:
            by_recipient.setdefault(rcpt, []).append(c)

    lookback_h = int(settings.VOBIZ_CDR_LOOKBACK_HOURS)
    window_s = int(settings.VOBIZ_CDR_MATCH_WINDOW_SECONDS)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, caller, started_at, telephony_paise
               FROM call_logs
               WHERE cdr_reconciled = FALSE
                 AND direction = 'outbound'
                 AND started_at >= now() - ($1 || ' hours')::interval
               ORDER BY started_at DESC
               LIMIT 500""",
            str(lookback_h),
        )

        reconciled = 0
        for row in rows:
            candidates = by_recipient.get(_last10(row["caller"]), [])
            best: Optional[dict] = None
            best_gap = window_s + 1
            for c in candidates:
                cstart = _cdr_start(c)
                if not cstart or not row["started_at"]:
                    continue
                gap = abs((cstart - row["started_at"]).total_seconds())
                if gap < best_gap:
                    best, best_gap = c, gap
            if best is None or best_gap > window_s:
                continue
            real_paise = _cdr_cost_paise(best)
            if real_paise is None:
                continue
            await conn.execute(
                """UPDATE call_logs
                   SET cost_paise      = cost_paise - telephony_paise + $1,
                       telephony_paise = $1,
                       cdr_reconciled  = TRUE
                   WHERE id = $2""",
                real_paise, row["id"],
            )
            reconciled += 1

    if reconciled:
        logger.info("vobiz_cdr_reconciled", count=reconciled)
    return reconciled
