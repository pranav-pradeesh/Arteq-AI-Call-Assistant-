"""
backend-additions/routes/analytics_api.py
==========================================
Analytics endpoints for the Arteq Hospital Voice Agent admin dashboard.

Router prefix : /admin
Tags          : analytics
Auth          : Bearer JWT via `require_auth` (see deps.py)

Endpoints
---------
GET /admin/hospitals/{hospital_id}/analytics
    Time-bucketed call metrics (by day or hour).

GET /admin/hospitals/{hospital_id}/analytics/summary
    Aggregate summary for a date window, including intent/outcome breakdowns
    and a comparison delta against the preceding equal-length window.

Wire-up in the real app
-----------------------
    from backend_additions.routes import analytics_api
    app.include_router(analytics_api.router)

To use the real _require_auth instead of the placeholder, either:
  a) Replace `require_auth` import with the real dependency, or
  b) Pass it at include_router time:
        app.include_router(
            analytics_api.router,
            dependencies=[Depends(real_require_auth)],
        )
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..deps import AuthDep, PoolDep, require_auth

router = APIRouter(prefix="/admin", tags=["analytics"])

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class AnalyticsPoint(BaseModel):
    """One time-bucket row returned by the bucketed analytics endpoint."""

    bucket: str = Field(..., description="ISO-8601 datetime string for the bucket start")
    calls: int
    avg_latency_ms: Optional[float] = None
    cost_paise: int


class OutcomeBreakdown(BaseModel):
    """Map of outcome label → call count."""

    __root__: Dict[str, int]

    class Config:
        # Allow arbitrary dict root
        arbitrary_types_allowed = True


class AnalyticsSummary(BaseModel):
    """Aggregate summary for a date window."""

    total_calls: int
    total_cost_paise: int
    avg_latency_ms: Optional[float] = None
    avg_turns: Optional[float] = None
    outcomes: Dict[str, int] = Field(
        default_factory=dict,
        description="Call count grouped by outcome label",
    )
    intents: Dict[str, int] = Field(
        default_factory=dict,
        description="Tallied intent labels across all calls in the window",
    )
    languages: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Call count by detected language. "
            # TODO: The call_logs table does not currently have a dedicated
            # `language` column. This field is populated from the `intents`
            # JSON array if a 'language:XX' token is present, otherwise it
            # is returned as an empty dict. Add a `language` column in a
            # future migration (005_language_column.sql) to make this reliable.
        ),
    )
    delta_calls_pct: Optional[float] = Field(
        None,
        description=(
            "Percentage change in total calls vs the immediately preceding "
            "equal-length window. Null when the preceding window has zero calls."
        ),
    )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

_VALID_BUCKETS = {"day", "hour"}


def _default_range() -> tuple[str, str]:
    """Return ISO date strings for [today-30d, today]."""
    today = date.today()
    return (today - timedelta(days=30)).isoformat(), today.isoformat()


def _parse_date_param(value: Optional[str], param_name: str) -> Optional[datetime]:
    """Parse an ISO date/datetime string from a query parameter."""
    if value is None:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Invalid date format for '{param_name}'. Expected YYYY-MM-DD or ISO-8601.",
    )


def _tally_intents(rows: list[asyncpg.Record]) -> Dict[str, int]:
    """
    Parse the `intents` JSON column (array of strings) from multiple rows
    and aggregate a tally dict.

    Example intents value: '["appointment_booking", "doctor_schedule"]'
    """
    tally: Dict[str, int] = {}
    for row in rows:
        raw = row.get("intents")
        if not raw:
            continue
        try:
            items: Any = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str):
                        tally[item] = tally.get(item, 0) + 1
            elif isinstance(items, dict):
                # Some rows may store {intent: count} directly
                for k, v in items.items():
                    tally[k] = tally.get(k, 0) + (v if isinstance(v, int) else 1)
        except (json.JSONDecodeError, TypeError):
            continue
    return tally


def _tally_languages(rows: list[asyncpg.Record]) -> Dict[str, int]:
    """
    Best-effort language tally from the `intents` JSON column.

    TODO: Once a `language` column is added to call_logs (migration 005),
    replace this function body with:
        return {row["language"]: count for row, count in ...}

    For now, we look for tokens shaped like "language:ml", "language:hi", etc.
    inside the intents array.  If no such tokens are present the result is {}.
    """
    tally: Dict[str, int] = {}
    for row in rows:
        raw = row.get("intents")
        if not raw:
            continue
        try:
            items: Any = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item.startswith("language:"):
                    lang = item.split(":", 1)[1].strip()
                    if lang:
                        tally[lang] = tally.get(lang, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    return tally


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/hospitals/{hospital_id}/analytics",
    response_model=List[AnalyticsPoint],
    summary="Time-bucketed call metrics",
)
async def get_analytics(
    hospital_id: str,
    pool: PoolDep,
    _auth: AuthDep,
    from_: Annotated[
        Optional[str],
        Query(alias="from", description="Start date (YYYY-MM-DD or ISO-8601)"),
    ] = None,
    to: Annotated[
        Optional[str],
        Query(description="End date (YYYY-MM-DD or ISO-8601)"),
    ] = None,
    bucket: Annotated[
        Literal["day", "hour"],
        Query(description="Time bucket granularity"),
    ] = "day",
) -> List[AnalyticsPoint]:
    """
    Return call metrics bucketed by day or hour for a given hospital and
    date range.  Defaults to the last 30 days when `from`/`to` are omitted.

    Each item in the response represents one bucket:
    - **bucket**: ISO-8601 string for the bucket start
    - **calls**: number of calls that started in this bucket
    - **avg_latency_ms**: mean latency across those calls (null if no data)
    - **cost_paise**: total cost in paise for that bucket
    """
    if bucket not in _VALID_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid bucket '{bucket}'. Must be 'day' or 'hour'.",
        )

    default_from, default_to = _default_range()
    dt_from = _parse_date_param(from_, "from") or _parse_date_param(default_from, "from")
    dt_to = _parse_date_param(to, "to") or _parse_date_param(default_to, "to")

    # Clamp: from must be before to
    if dt_from >= dt_to:  # type: ignore[operator]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'from' must be earlier than 'to'.",
        )

    # Limit the range to 366 days to prevent accidental full-table scans
    if (dt_to - dt_from).days > 366:  # type: ignore[operator]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Date range must not exceed 366 days.",
        )

    # The `bucket` value is safe — validated against _VALID_BUCKETS above,
    # so it is fine to interpolate it into the SQL string (it is not user text).
    sql = f"""
        SELECT
            date_trunc($3, started_at)          AS bucket,
            COUNT(*)                            AS calls,
            AVG(latency_avg_ms)                 AS avg_latency_ms,
            COALESCE(SUM(cost_paise), 0)        AS cost_paise
        FROM call_logs
        WHERE hospital_id = $1
          AND started_at >= $2
          AND started_at <  $4
        GROUP BY 1
        ORDER BY 1 ASC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, hospital_id, dt_from, bucket, dt_to)

    return [
        AnalyticsPoint(
            bucket=row["bucket"].isoformat(),
            calls=row["calls"],
            avg_latency_ms=(
                float(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None
            ),
            cost_paise=int(row["cost_paise"]),
        )
        for row in rows
    ]


@router.get(
    "/hospitals/{hospital_id}/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Aggregate analytics summary with delta comparison",
)
async def get_analytics_summary(
    hospital_id: str,
    pool: PoolDep,
    _auth: AuthDep,
    from_: Annotated[
        Optional[str],
        Query(alias="from", description="Start date (YYYY-MM-DD or ISO-8601)"),
    ] = None,
    to: Annotated[
        Optional[str],
        Query(description="End date (YYYY-MM-DD or ISO-8601)"),
    ] = None,
) -> AnalyticsSummary:
    """
    Return an aggregate summary for a hospital over the given date window.

    In addition to totals and averages, the response includes:
    - **outcomes**: call count grouped by the `outcome` column
    - **intents**: tallied intent labels parsed from each row's `intents` JSON
    - **languages**: best-effort tally (see TODO in `_tally_languages`)
    - **delta_calls_pct**: % change vs the immediately preceding equal window
      (e.g. if the window is 7 days, compare against the prior 7 days)
    """
    default_from, default_to = _default_range()
    dt_from: datetime = _parse_date_param(from_, "from") or _parse_date_param(default_from, "from")  # type: ignore[assignment]
    dt_to: datetime = _parse_date_param(to, "to") or _parse_date_param(default_to, "to")  # type: ignore[assignment]

    if dt_from >= dt_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'from' must be earlier than 'to'.",
        )

    window_length: timedelta = dt_to - dt_from
    prev_from: datetime = dt_from - window_length
    prev_to: datetime = dt_from  # non-overlapping

    async with pool.acquire() as conn:
        # ------------------------------------------------------------------
        # Current window: aggregate stats
        # ------------------------------------------------------------------
        agg_sql = """
            SELECT
                COUNT(*)                    AS total_calls,
                COALESCE(SUM(cost_paise), 0) AS total_cost_paise,
                AVG(latency_avg_ms)         AS avg_latency_ms,
                AVG(total_turns)            AS avg_turns
            FROM call_logs
            WHERE hospital_id = $1
              AND started_at >= $2
              AND started_at <  $3
        """
        agg_row = await conn.fetchrow(agg_sql, hospital_id, dt_from, dt_to)

        # ------------------------------------------------------------------
        # Current window: per-row data for intent / language / outcome tallies
        # ------------------------------------------------------------------
        detail_sql = """
            SELECT outcome, intents
            FROM call_logs
            WHERE hospital_id = $1
              AND started_at >= $2
              AND started_at <  $3
        """
        detail_rows = await conn.fetch(detail_sql, hospital_id, dt_from, dt_to)

        # ------------------------------------------------------------------
        # Previous window: call count only (for delta)
        # ------------------------------------------------------------------
        prev_count_sql = """
            SELECT COUNT(*) AS total_calls
            FROM call_logs
            WHERE hospital_id = $1
              AND started_at >= $2
              AND started_at <  $3
        """
        prev_row = await conn.fetchrow(prev_count_sql, hospital_id, prev_from, prev_to)

    # ------------------------------------------------------------------
    # Outcome breakdown
    # ------------------------------------------------------------------
    outcomes: Dict[str, int] = {}
    for row in detail_rows:
        outcome = row.get("outcome")
        if outcome:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

    # ------------------------------------------------------------------
    # Intent tally
    # ------------------------------------------------------------------
    intents = _tally_intents(detail_rows)

    # ------------------------------------------------------------------
    # Language tally (best-effort — see TODO in helper)
    # ------------------------------------------------------------------
    languages = _tally_languages(detail_rows)

    # ------------------------------------------------------------------
    # Delta calculation
    # ------------------------------------------------------------------
    current_calls: int = agg_row["total_calls"] if agg_row else 0
    prev_calls: int = prev_row["total_calls"] if prev_row else 0

    delta_calls_pct: Optional[float] = None
    if prev_calls > 0:
        delta_calls_pct = round(((current_calls - prev_calls) / prev_calls) * 100, 2)
    elif current_calls > 0:
        # Previous window had zero calls; define as +inf conceptually,
        # but return None to avoid misleading large numbers in the UI.
        delta_calls_pct = None

    return AnalyticsSummary(
        total_calls=current_calls,
        total_cost_paise=int(agg_row["total_cost_paise"]) if agg_row else 0,
        avg_latency_ms=(
            float(agg_row["avg_latency_ms"])
            if agg_row and agg_row["avg_latency_ms"] is not None
            else None
        ),
        avg_turns=(
            float(agg_row["avg_turns"])
            if agg_row and agg_row["avg_turns"] is not None
            else None
        ),
        outcomes=outcomes,
        intents=intents,
        languages=languages,
        delta_calls_pct=delta_calls_pct,
    )
