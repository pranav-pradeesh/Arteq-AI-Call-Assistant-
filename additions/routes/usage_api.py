"""
Usage & cost API — per-hospital spend vs. plan limit, broken out per service.

Cost is computed from each call's stored per-service paise (call_logs), which the
agent writes from REAL usage (tokens / audio-seconds / characters × published
rate) and the Vobiz CDR job reconciles to the REAL billed telephony cost. Each
figure carries a `source`:
  • "billed"     — the real cost charged by the platform (Vobiz CDR; OpenRouter)
  • "list-price" — real measured usage × the provider's published price
                   (Gemini / Sarvam expose no per-call cost API)

Routes:
  GET /admin/hospitals/{hospital_id}/usage   current-period usage vs limit (scoped)
  GET /admin/usage/overview                  all hospitals' usage (super_admin)
  PUT /admin/hospitals/{hospital_id}/plan    set plan + limits (super_admin)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..deps import AuthDep, PoolDep, require_hospital_access, require_role

router = APIRouter(prefix="/admin", tags=["usage"])


# ── Billing period ────────────────────────────────────────────────────────────

def _add_months(dt: datetime, months: int) -> datetime:
    m = dt.month - 1 + months
    y = dt.year + m // 12
    return dt.replace(year=y, month=m % 12 + 1)


def billing_period(cycle_day: int, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """[start, end) of the current billing month anchored on cycle_day (1..28)."""
    now = now or datetime.now(timezone.utc)
    day = min(max(int(cycle_day or 1), 1), 28)
    anchor = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if now < anchor:
        return _add_months(anchor, -1), anchor
    return anchor, _add_months(anchor, 1)


# ── Response models ───────────────────────────────────────────────────────────

class ServiceLine(BaseModel):
    paise: int
    source: str  # "billed" | "list-price"


class UsageBreakdown(BaseModel):
    stt: ServiceLine
    tts: ServiceLine
    llm: ServiceLine
    telephony: ServiceLine


class UsageResponse(BaseModel):
    hospital_id: str
    hospital_name: Optional[str] = None
    plan_name: Optional[str] = None
    period_start: str
    period_end: str
    calls: int
    inbound_calls: int
    outbound_calls: int
    minutes: float
    cost_paise: int
    by_service: UsageBreakdown
    monthly_call_limit: Optional[int] = None
    monthly_minutes_limit: Optional[int] = None
    monthly_cost_limit_paise: Optional[int] = None
    price_per_minute_paise: Optional[int] = None
    amount_due_paise: Optional[int] = None
    percent_used: Optional[float] = None
    over_limit: bool = False


# ── Aggregation ───────────────────────────────────────────────────────────────

_AGG = """
    SELECT
      COUNT(*)                                                   AS calls,
      COUNT(*) FILTER (WHERE direction = 'inbound')              AS inbound,
      COUNT(*) FILTER (WHERE direction = 'outbound')             AS outbound,
      COALESCE(SUM(EXTRACT(EPOCH FROM (ended_at - started_at))), 0) AS seconds,
      COALESCE(SUM(cost_paise), 0)                               AS cost_paise,
      COALESCE(SUM(stt_paise), 0)                                AS stt,
      COALESCE(SUM(tts_paise), 0)                                AS tts,
      COALESCE(SUM(llm_paise), 0)                                AS llm,
      COALESCE(SUM(telephony_paise), 0)                          AS tel,
      COUNT(*) FILTER (WHERE direction = 'outbound' AND cdr_reconciled) AS tel_billed
    FROM call_logs
    WHERE hospital_id = $1 AND started_at >= $2 AND started_at < $3
"""


def _percent_and_over(calls, minutes, cost_paise, lim_calls, lim_min, lim_cost):
    ratios = []
    if lim_calls:
        ratios.append(calls / lim_calls)
    if lim_min:
        ratios.append(minutes / lim_min)
    if lim_cost:
        ratios.append(cost_paise / lim_cost)
    if not ratios:
        return None, False
    top = max(ratios)
    return round(top * 100, 1), top >= 1.0


async def _usage_for(conn, hosp: dict) -> UsageResponse:
    start, end = billing_period(hosp.get("billing_cycle_day") or 1)
    row = await conn.fetchrow(_AGG, hosp["id"], start, end)
    calls = int(row["calls"])
    minutes = round(float(row["seconds"]) / 60.0, 1)
    cost_paise = int(row["cost_paise"])
    # Telephony is "billed" only if every outbound call this period was CDR-
    # reconciled; otherwise some of it is still the duration estimate.
    tel_billed = int(row["tel_billed"]) == int(row["outbound"]) and int(row["outbound"]) > 0
    pct, over = _percent_and_over(
        calls, minutes, cost_paise,
        hosp.get("monthly_call_limit"), hosp.get("monthly_minutes_limit"),
        hosp.get("monthly_cost_limit_paise"),
    )
    return UsageResponse(
        hospital_id=str(hosp["id"]),
        hospital_name=hosp.get("name"),
        plan_name=hosp.get("plan_name"),
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        calls=calls,
        inbound_calls=int(row["inbound"]),
        outbound_calls=int(row["outbound"]),
        minutes=minutes,
        cost_paise=cost_paise,
        by_service=UsageBreakdown(
            stt=ServiceLine(paise=int(row["stt"]), source="list-price"),
            tts=ServiceLine(paise=int(row["tts"]), source="list-price"),
            llm=ServiceLine(paise=int(row["llm"]), source="list-price"),
            telephony=ServiceLine(
                paise=int(row["tel"]), source="billed" if tel_billed else "list-price"
            ),
        ),
        monthly_call_limit=hosp.get("monthly_call_limit"),
        monthly_minutes_limit=hosp.get("monthly_minutes_limit"),
        monthly_cost_limit_paise=hosp.get("monthly_cost_limit_paise"),
        price_per_minute_paise=hosp.get("price_per_minute_paise"),
        amount_due_paise=(
            int(round(minutes * hosp["price_per_minute_paise"]))
            if hosp.get("price_per_minute_paise") else None
        ),
        percent_used=pct,
        over_limit=over,
    )


_HOSP_COLS = (
    "id, name, plan_name, monthly_call_limit, monthly_minutes_limit, "
    "monthly_cost_limit_paise, price_per_minute_paise, billing_cycle_day, subscription_status"
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/hospitals/{hospital_id}/usage",
    response_model=UsageResponse,
    summary="Current-period usage and cost vs plan limit for one hospital",
    dependencies=[Depends(require_hospital_access)],
)
async def hospital_usage(hospital_id: str, pool: PoolDep, _auth: AuthDep) -> UsageResponse:
    async with pool.acquire() as conn:
        hosp = await conn.fetchrow(
            f"SELECT {_HOSP_COLS} FROM hospitals WHERE id = $1", hospital_id
        )
    if not hosp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hospital not found.")
    async with pool.acquire() as conn:
        return await _usage_for(conn, dict(hosp))


@router.get(
    "/usage/overview",
    response_model=list[UsageResponse],
    summary="Usage and cost for every hospital (super_admin)",
    dependencies=[Depends(require_role("super_admin"))],
)
async def usage_overview(pool: PoolDep, _auth: AuthDep) -> list[UsageResponse]:
    async with pool.acquire() as conn:
        hosps = await conn.fetch(f"SELECT {_HOSP_COLS} FROM hospitals ORDER BY name")
        out = []
        for h in hosps:
            out.append(await _usage_for(conn, dict(h)))
    return out


class PlanIn(BaseModel):
    plan_name: Optional[str] = Field(None, description="e.g. trial | starter | growth")
    monthly_call_limit: Optional[int] = Field(None, ge=0)
    monthly_minutes_limit: Optional[int] = Field(None, ge=0)
    monthly_cost_limit_paise: Optional[int] = Field(None, ge=0)
    price_per_minute_paise: Optional[int] = Field(None, ge=0)
    billing_cycle_day: Optional[int] = Field(None, ge=1, le=28)


@router.put(
    "/hospitals/{hospital_id}/plan",
    summary="Set a hospital's plan + monthly limits (super_admin)",
    dependencies=[Depends(require_role("super_admin"))],
)
async def set_plan(hospital_id: str, body: PlanIn, pool: PoolDep) -> dict:
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM hospitals WHERE id = $1", hospital_id)
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hospital not found.")
        await conn.execute(
            """UPDATE hospitals SET
                   plan_name                = COALESCE($2, plan_name),
                   monthly_call_limit       = $3,
                   monthly_minutes_limit    = $4,
                   monthly_cost_limit_paise = $5,
                   price_per_minute_paise   = $6,
                   billing_cycle_day        = COALESCE($7, billing_cycle_day)
               WHERE id = $1""",
            hospital_id, body.plan_name, body.monthly_call_limit,
            body.monthly_minutes_limit, body.monthly_cost_limit_paise,
            body.price_per_minute_paise, body.billing_cycle_day,
        )
        hosp = await conn.fetchrow(f"SELECT {_HOSP_COLS} FROM hospitals WHERE id = $1", hospital_id)
        usage = await _usage_for(conn, dict(hosp))
    return usage.model_dump()
