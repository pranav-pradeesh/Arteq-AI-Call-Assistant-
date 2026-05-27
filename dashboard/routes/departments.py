"""Department management API."""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from dashboard.routes.auth import get_current_user
from src.cache.redis_client import invalidate_tenant_cache
from src.db.connection import get_db_session
from src.db.models import (
    ConsultationFee, DashboardUser, Department, DepartmentTiming,
    DayOfWeek, HospitalBranch, Tenant
)

router = APIRouter()


class TimingIn(BaseModel):
    day_of_week: str
    open_time: str    # "HH:MM"
    close_time: str
    is_closed: bool = False
    session_label: Optional[str] = None


class FeeIn(BaseModel):
    fee_type: str = "consultation"
    amount: float
    currency: str = "INR"
    notes: Optional[str] = None


class DepartmentIn(BaseModel):
    name: str
    aliases: List[str] = []
    is_active: bool = True
    floor_info: Optional[str] = None
    room_number: Optional[str] = None
    timings: List[TimingIn] = []
    fees: List[FeeIn] = []


class DepartmentOut(BaseModel):
    id: str
    branch_id: str
    name: str
    normalized_name: str
    aliases: List[str]
    is_active: bool
    floor_info: Optional[str]
    room_number: Optional[str]


@router.get("/{tenant_slug}/branch/{branch_id}", response_model=List[DepartmentOut])
async def list_departments(
    tenant_slug: str,
    branch_id: UUID,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Department)
            .join(HospitalBranch)
            .join(Tenant)
            .where(HospitalBranch.id == branch_id, Tenant.slug == tenant_slug)
        )
        depts = result.scalars().all()
        return [
            DepartmentOut(
                id=str(d.id),
                branch_id=str(d.branch_id),
                name=d.name,
                normalized_name=d.normalized_name,
                aliases=d.aliases or [],
                is_active=d.is_active,
                floor_info=d.floor_info,
                room_number=d.room_number,
            )
            for d in depts
        ]


@router.post("/{tenant_slug}/branch/{branch_id}")
async def create_department(
    tenant_slug: str,
    branch_id: UUID,
    data: DepartmentIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    import re
    from datetime import time

    async with get_db_session() as session:
        result = await session.execute(
            select(HospitalBranch).join(Tenant)
            .where(HospitalBranch.id == branch_id, Tenant.slug == tenant_slug)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Branch not found")

        normalized = re.sub(r"[^a-z0-9]", "", data.name.lower())
        dept = Department(
            branch_id=branch_id,
            name=data.name,
            normalized_name=normalized,
            aliases=data.aliases,
            is_active=data.is_active,
            floor_info=data.floor_info,
            room_number=data.room_number,
        )
        session.add(dept)
        await session.flush()

        for t in data.timings:
            timing = DepartmentTiming(
                department_id=dept.id,
                day_of_week=DayOfWeek(t.day_of_week),
                open_time=time.fromisoformat(t.open_time),
                close_time=time.fromisoformat(t.close_time),
                is_closed=t.is_closed,
                session_label=t.session_label,
            )
            session.add(timing)

        for f in data.fees:
            fee = ConsultationFee(
                branch_id=branch_id,
                department_id=dept.id,
                fee_type=f.fee_type,
                amount=f.amount,
                currency=f.currency,
                notes=f.notes,
            )
            session.add(fee)

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "created", "department_id": str(dept.id)}


@router.put("/{tenant_slug}/departments/{dept_id}")
async def update_department(
    tenant_slug: str,
    dept_id: UUID,
    data: DepartmentIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Department).join(HospitalBranch).join(Tenant)
            .where(Department.id == dept_id, Tenant.slug == tenant_slug)
        )
        dept = result.scalar_one_or_none()
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found")

        dept.name = data.name
        dept.aliases = data.aliases
        dept.is_active = data.is_active
        dept.floor_info = data.floor_info
        dept.room_number = data.room_number

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "updated"}
