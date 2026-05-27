"""Doctor management API — CRUD with cache invalidation."""
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
    DashboardUser, Doctor, DoctorAvailability, HospitalBranch, Tenant
)

router = APIRouter()


class DoctorAvailabilityIn(BaseModel):
    day_of_week: str   # "monday", "tuesday", etc.
    start_time: str    # "HH:MM"
    end_time: str      # "HH:MM"
    is_available: bool = True
    slot_notes: Optional[str] = None


class DoctorIn(BaseModel):
    name: str
    qualification: Optional[str] = None
    specialization: Optional[str] = None
    aliases: List[str] = []
    is_active: bool = True
    is_visiting: bool = False
    department_id: Optional[str] = None
    availability: List[DoctorAvailabilityIn] = []


class DoctorOut(BaseModel):
    id: str
    branch_id: str
    name: str
    qualification: Optional[str]
    specialization: Optional[str]
    aliases: List[str]
    is_active: bool
    is_visiting: bool
    department_id: Optional[str]

    class Config:
        from_attributes = True


def _get_tenant_slug_for_branch(session, branch_id: UUID) -> Optional[str]:
    """Helper: get tenant slug for a branch."""
    # This is called within async context
    pass


@router.get("/{tenant_slug}/branch/{branch_id}", response_model=List[DoctorOut])
async def list_doctors(
    tenant_slug: str,
    branch_id: UUID,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Doctor)
            .join(HospitalBranch)
            .join(Tenant)
            .where(
                HospitalBranch.id == branch_id,
                Tenant.slug == tenant_slug,
            )
            .options(selectinload(Doctor.availability))
        )
        doctors = result.scalars().all()
        return [
            DoctorOut(
                id=str(d.id),
                branch_id=str(d.branch_id),
                name=d.name,
                qualification=d.qualification,
                specialization=d.specialization,
                aliases=d.aliases or [],
                is_active=d.is_active,
                is_visiting=d.is_visiting,
                department_id=str(d.department_id) if d.department_id else None,
            )
            for d in doctors
        ]


@router.post("/{tenant_slug}/branch/{branch_id}", response_model=DoctorOut)
async def create_doctor(
    tenant_slug: str,
    branch_id: UUID,
    data: DoctorIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        # Verify branch belongs to tenant
        result = await session.execute(
            select(HospitalBranch)
            .join(Tenant)
            .where(HospitalBranch.id == branch_id, Tenant.slug == tenant_slug)
        )
        branch = result.scalar_one_or_none()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        import re
        normalized = re.sub(r"[^a-z0-9 ]", "", data.name.lower()).strip()

        doctor = Doctor(
            branch_id=branch_id,
            name=data.name,
            normalized_name=normalized,
            qualification=data.qualification,
            specialization=data.specialization,
            aliases=data.aliases,
            is_active=data.is_active,
            is_visiting=data.is_visiting,
            department_id=UUID(data.department_id) if data.department_id else None,
        )
        session.add(doctor)
        await session.flush()  # get doctor.id

        # Add availability slots
        for slot in data.availability:
            from src.db.models import DayOfWeek
            from datetime import time
            start = time.fromisoformat(slot.start_time)
            end = time.fromisoformat(slot.end_time)
            avail = DoctorAvailability(
                doctor_id=doctor.id,
                day_of_week=DayOfWeek(slot.day_of_week),
                start_time=start,
                end_time=end,
                is_available=slot.is_available,
                slot_notes=slot.slot_notes,
            )
            session.add(avail)

    await invalidate_tenant_cache(tenant_slug)
    return DoctorOut(
        id=str(doctor.id),
        branch_id=str(branch_id),
        name=doctor.name,
        qualification=doctor.qualification,
        specialization=doctor.specialization,
        aliases=doctor.aliases or [],
        is_active=doctor.is_active,
        is_visiting=doctor.is_visiting,
        department_id=data.department_id,
    )


@router.put("/{tenant_slug}/doctors/{doctor_id}")
async def update_doctor(
    tenant_slug: str,
    doctor_id: UUID,
    data: DoctorIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Doctor)
            .join(HospitalBranch)
            .join(Tenant)
            .where(Doctor.id == doctor_id, Tenant.slug == tenant_slug)
        )
        doctor = result.scalar_one_or_none()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        doctor.name = data.name
        doctor.qualification = data.qualification
        doctor.specialization = data.specialization
        doctor.aliases = data.aliases
        doctor.is_active = data.is_active
        doctor.is_visiting = data.is_visiting

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "updated", "doctor_id": str(doctor_id)}


@router.delete("/{tenant_slug}/doctors/{doctor_id}")
async def deactivate_doctor(
    tenant_slug: str,
    doctor_id: UUID,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Doctor)
            .join(HospitalBranch)
            .join(Tenant)
            .where(Doctor.id == doctor_id, Tenant.slug == tenant_slug)
        )
        doctor = result.scalar_one_or_none()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")
        doctor.is_active = False

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "deactivated"}
