"""
backend-additions/routes/monitoring_api.py
===========================================
Live-monitoring endpoint (polling v1).

The frontend `/live` page polls `GET /admin/hospitals/{id}/active-calls`
every few seconds. This v1 reads in-progress calls straight from `call_logs`
(rows where `ended_at IS NULL`), so it works as soon as the agent writes a
`call_logs` row at call START and updates `ended_at` at hang-up.

Upgrade path (plan §4.3): replace polling with a WebSocket
(`/admin/ws/live`) fed by Redis pub/sub, where the agent publishes
start/turn/end events. The frontend already degrades gracefully if this
endpoint is missing, so shipping the poll first is safe.
"""

from __future__ import annotations

from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, Path

from ..deps import PoolDep, require_auth

router = APIRouter(prefix="/admin", tags=["monitoring"])


@router.get(
    "/hospitals/{hospital_id}/active-calls",
    summary="In-progress calls (polling v1)",
    dependencies=[Depends(require_auth)],
)
async def active_calls(
    pool: PoolDep,
    hospital_id: Annotated[str, Path(description="Hospital UUID")],
) -> List[dict[str, Any]]:
    """Return calls that have started but not yet ended for this hospital."""
    sql = """
        SELECT id, hospital_id, call_id, caller, started_at, ended_at,
               total_turns, latency_avg_ms, cost_paise, outcome, intents
        FROM call_logs
        WHERE hospital_id = $1
          AND started_at IS NOT NULL
          AND ended_at IS NULL
        ORDER BY started_at DESC
        LIMIT 50
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, hospital_id)

    result: List[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k in ("id", "hospital_id", "call_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("started_at", "ended_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        result.append(d)
    return result
