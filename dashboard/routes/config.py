"""System config and holiday management."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from dashboard.routes.auth import get_current_user
from src.cache.redis_client import invalidate_tenant_cache
from src.db.connection import get_db_session
from src.db.models import (
    BranchDayPolicy, DashboardUser, DayOfWeek,
    HolidayOverride, HospitalBranch, KeywordRule, Tenant
)

router = APIRouter()


class HolidayIn(BaseModel):
    override_date: str  # "YYYY-MM-DD"
    is_closed: bool = True
    reason: Optional[str] = None
    emergency_only: bool = False
    notes: Optional[str] = None


class DayPolicyIn(BaseModel):
    day_of_week: str
    is_open: bool
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    notes: Optional[str] = None


class KeywordRuleIn(BaseModel):
    keyword: str
    maps_to_intent: str
    maps_to_entity: Optional[str] = None
    weight: float = 1.0
    is_active: bool = True


# ── Holidays ──────────────────────────────────────────────────────────────────

@router.post("/{tenant_slug}/branches/{branch_id}/holidays")
async def add_holiday(
    tenant_slug: str,
    branch_id: UUID,
    data: HolidayIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    from datetime import timezone
    async with get_db_session() as session:
        holiday = HolidayOverride(
            branch_id=branch_id,
            override_date=datetime.strptime(data.override_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ),
            is_closed=data.is_closed,
            reason=data.reason,
            emergency_only=data.emergency_only,
            notes=data.notes,
        )
        session.add(holiday)
    await invalidate_tenant_cache(tenant_slug)
    return {"status": "created"}


@router.get("/{tenant_slug}/branches/{branch_id}/holidays")
async def list_holidays(
    tenant_slug: str,
    branch_id: UUID,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(HolidayOverride).where(HolidayOverride.branch_id == branch_id)
            .order_by(HolidayOverride.override_date)
        )
        holidays = result.scalars().all()
        return [
            {
                "id": str(h.id),
                "date": h.override_date.strftime("%Y-%m-%d"),
                "is_closed": h.is_closed,
                "reason": h.reason,
                "emergency_only": h.emergency_only,
            }
            for h in holidays
        ]


# ── Day policies ──────────────────────────────────────────────────────────────

@router.put("/{tenant_slug}/branches/{branch_id}/day-policy")
async def set_day_policy(
    tenant_slug: str,
    branch_id: UUID,
    data: List[DayPolicyIn],
    current_user: DashboardUser = Depends(get_current_user),
):
    from datetime import time
    async with get_db_session() as session:
        for policy_data in data:
            result = await session.execute(
                select(BranchDayPolicy).where(
                    BranchDayPolicy.branch_id == branch_id,
                    BranchDayPolicy.day_of_week == DayOfWeek(policy_data.day_of_week),
                )
            )
            policy = result.scalar_one_or_none()
            if policy:
                policy.is_open = policy_data.is_open
                policy.open_time = (
                    time.fromisoformat(policy_data.open_time) if policy_data.open_time else None
                )
                policy.close_time = (
                    time.fromisoformat(policy_data.close_time) if policy_data.close_time else None
                )
                policy.notes = policy_data.notes
            else:
                new_policy = BranchDayPolicy(
                    branch_id=branch_id,
                    day_of_week=DayOfWeek(policy_data.day_of_week),
                    is_open=policy_data.is_open,
                    open_time=(
                        time.fromisoformat(policy_data.open_time) if policy_data.open_time else None
                    ),
                    close_time=(
                        time.fromisoformat(policy_data.close_time) if policy_data.close_time else None
                    ),
                    notes=policy_data.notes,
                )
                session.add(new_policy)

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "updated", "policies_set": len(data)}


# ── Keyword rules ─────────────────────────────────────────────────────────────

@router.post("/{tenant_slug}/keyword-rules")
async def add_keyword_rule(
    tenant_slug: str,
    data: KeywordRuleIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == tenant_slug)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        rule = KeywordRule(
            tenant_id=tenant.id,
            keyword=data.keyword.lower(),
            maps_to_intent=data.maps_to_intent,
            maps_to_entity=data.maps_to_entity,
            weight=data.weight,
            is_active=data.is_active,
        )
        session.add(rule)

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "created"}


@router.post("/{tenant_slug}/cache/clear")
async def clear_tenant_cache(
    tenant_slug: str,
    current_user: DashboardUser = Depends(get_current_user),
):
    """Force cache invalidation — next call will reload from DB."""
    await invalidate_tenant_cache(tenant_slug)
    return {"status": "cache_cleared", "tenant": tenant_slug}


@router.get("/{tenant_slug}/keyword-rules")
async def list_keyword_rules(
    tenant_slug: str,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(KeywordRule)
            .join(Tenant)
            .where(Tenant.slug == tenant_slug)
        )
        rules = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "keyword": r.keyword,
                "maps_to_intent": r.maps_to_intent,
                "maps_to_entity": r.maps_to_entity,
                "weight": r.weight,
                "is_active": r.is_active,
            }
            for r in rules
        ]
