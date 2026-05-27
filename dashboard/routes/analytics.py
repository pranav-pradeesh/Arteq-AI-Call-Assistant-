"""Call analytics and reporting endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from dashboard.routes.auth import get_current_user
from src.db.connection import get_db_session
from src.db.models import CallLog, DashboardUser

router = APIRouter()


@router.get("/{tenant_slug}/summary")
async def get_analytics_summary(
    tenant_slug: str,
    days: int = 7,
    current_user: DashboardUser = Depends(get_current_user),
):
    """Summary metrics for the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with get_db_session() as session:
        result = await session.execute(
            select(
                func.count(CallLog.id).label("total_calls"),
                func.avg(CallLog.total_latency_ms).label("avg_latency_ms"),
                func.sum(
                    func.cast(CallLog.transferred_to_human, func.Integer())
                ).label("transfers"),
                func.avg(CallLog.clarification_count).label("avg_clarifications"),
            ).where(CallLog.call_start >= since)
        )
        row = result.one()

        # Intent distribution
        intent_result = await session.execute(
            select(CallLog.detected_intent, func.count(CallLog.id).label("count"))
            .where(CallLog.call_start >= since)
            .group_by(CallLog.detected_intent)
            .order_by(func.count(CallLog.id).desc())
        )
        intents = [
            {"intent": row.detected_intent, "count": row.count}
            for row in intent_result
        ]

    return {
        "period_days": days,
        "total_calls": row.total_calls or 0,
        "avg_latency_ms": round(row.avg_latency_ms or 0, 1),
        "total_transfers": row.transfers or 0,
        "avg_clarifications_per_call": round(row.avg_clarifications or 0, 2),
        "top_intents": intents[:10],
    }


@router.get("/{tenant_slug}/calls")
async def list_recent_calls(
    tenant_slug: str,
    limit: int = 50,
    current_user: DashboardUser = Depends(get_current_user),
):
    """Recent call log entries for debugging."""
    async with get_db_session() as session:
        result = await session.execute(
            select(CallLog)
            .order_by(CallLog.created_at.desc())
            .limit(min(limit, 200))
        )
        calls = result.scalars().all()
        return [
            {
                "call_id": c.call_id,
                "start": c.call_start.isoformat() if c.call_start else None,
                "duration_ms": c.duration_ms,
                "intent": c.detected_intent,
                "confidence": c.intent_confidence,
                "outcome": c.outcome.value if c.outcome else None,
                "clarifications": c.clarification_count,
                "transferred": c.transferred_to_human,
                "total_latency_ms": c.total_latency_ms,
                "errors": c.errors_encountered,
            }
            for c in calls
        ]
