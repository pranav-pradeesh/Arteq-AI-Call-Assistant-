"""
Hospital branch management API.
All writes invalidate the tenant cache immediately.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from dashboard.routes.auth import get_current_user
from src.cache.redis_client import invalidate_tenant_cache
from src.db.connection import get_db_session
from src.db.models import DashboardUser, HospitalBranch, Tenant

router = APIRouter()


class BranchIn(BaseModel):
    name: str
    is_main_branch: bool = False
    address: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    phone_primary: Optional[str] = None
    phone_secondary: Optional[str] = None
    phone_emergency: Optional[str] = None
    whatsapp: Optional[str] = None
    has_emergency: bool = False
    emergency_24x7: bool = False
    emergency_notes: Optional[str] = None
    general_open_time: Optional[str] = None   # "HH:MM"
    general_close_time: Optional[str] = None


class BranchOut(BranchIn):
    id: str
    tenant_id: str

    class Config:
        from_attributes = True


@router.get("/{tenant_slug}/branches", response_model=List[BranchOut])
async def list_branches(
    tenant_slug: str,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(HospitalBranch)
            .join(Tenant)
            .where(Tenant.slug == tenant_slug)
        )
        branches = result.scalars().all()
        return [
            BranchOut(
                id=str(b.id),
                tenant_id=str(b.tenant_id),
                name=b.name,
                is_main_branch=b.is_main_branch,
                address=b.address,
                city=b.city,
                district=b.district,
                phone_primary=b.phone_primary,
                phone_secondary=b.phone_secondary,
                phone_emergency=b.phone_emergency,
                whatsapp=b.whatsapp,
                has_emergency=b.has_emergency,
                emergency_24x7=b.emergency_24x7,
                emergency_notes=b.emergency_notes,
            )
            for b in branches
        ]


@router.put("/{tenant_slug}/branches/{branch_id}")
async def update_branch(
    tenant_slug: str,
    branch_id: UUID,
    data: BranchIn,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(HospitalBranch)
            .join(Tenant)
            .where(
                HospitalBranch.id == branch_id,
                Tenant.slug == tenant_slug,
            )
        )
        branch = result.scalar_one_or_none()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(branch, field, value)

    # Invalidate cache so runtime picks up the change immediately
    await invalidate_tenant_cache(tenant_slug)
    return {"status": "updated", "branch_id": str(branch_id)}


@router.get("/{tenant_slug}/profile")
async def get_tenant_profile(
    tenant_slug: str,
    current_user: DashboardUser = Depends(get_current_user),
):
    async with get_db_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == tenant_slug)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "transfer_number": tenant.transfer_number,
            "greeting_text": tenant.greeting_text,
            "fallback_text": tenant.fallback_text,
            "default_language": tenant.default_language,
        }


@router.put("/{tenant_slug}/profile")
async def update_tenant_profile(
    tenant_slug: str,
    data: dict,
    current_user: DashboardUser = Depends(get_current_user),
):
    allowed_fields = {
        "name", "transfer_number", "greeting_text",
        "fallback_text", "tts_voice", "stt_language_code",
    }
    async with get_db_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == tenant_slug)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        for field, value in data.items():
            if field in allowed_fields:
                setattr(tenant, field, value)

    await invalidate_tenant_cache(tenant_slug)
    return {"status": "updated"}
