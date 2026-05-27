"""
Tenant configuration loader.

Loading strategy (O(1) for hot path):
  1. Check Redis cache → return immediately if hit
  2. Query PostgreSQL → build TenantConfig → store in cache
  3. Return config

Cache invalidation happens on every dashboard update.
Each TenantConfig is a complete self-contained snapshot —
the runtime never queries the DB mid-call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.cache.redis_client import get_tenant_config_cache, set_tenant_config_cache
from src.db.connection import get_db_session
from src.db.models import (
    ConsultationFee,
    Department,
    DepartmentTiming,
    Doctor,
    DoctorAvailability,
    HospitalBranch,
    KeywordRule,
    Tenant,
)


# ─────────────────────────────────────────────────────────────────────────────
# Typed config objects — serializable for Redis storage
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DoctorInfo:
    id: str
    name: str
    normalized_name: str
    aliases: list[str]
    specialization: Optional[str]
    department_id: Optional[str]
    is_active: bool
    is_visiting: bool
    availability: list[dict]      # [{day, start_time, end_time, is_available}]
    fees: list[dict]              # [{fee_type, amount, currency}]


@dataclass
class DepartmentInfo:
    id: str
    name: str
    normalized_name: str
    aliases: list[str]
    is_active: bool
    floor_info: Optional[str]
    room_number: Optional[str]
    timings: list[dict]           # [{day, open_time, close_time, is_closed}]
    fees: list[dict]              # [{fee_type, amount, currency}]


@dataclass
class BranchInfo:
    id: str
    name: str
    is_main_branch: bool
    address: Optional[str]
    city: Optional[str]
    district: Optional[str]
    phone_primary: Optional[str]
    phone_secondary: Optional[str]
    phone_emergency: Optional[str]
    has_emergency: bool
    emergency_24x7: bool
    emergency_notes: Optional[str]
    general_open_time: Optional[str]   # "HH:MM"
    general_close_time: Optional[str]
    departments: list[DepartmentInfo] = field(default_factory=list)
    doctors: list[DoctorInfo] = field(default_factory=list)
    day_policies: list[dict] = field(default_factory=list)
    holiday_overrides: list[dict] = field(default_factory=list)

    def get_department(self, normalized_name: str) -> Optional[DepartmentInfo]:
        """O(n) lookup — n is small (< 30 depts per branch)."""
        for d in self.departments:
            if d.normalized_name == normalized_name:
                return d
            if normalized_name in d.aliases:
                return d
        return None

    def get_doctor(self, name_or_alias: str) -> Optional[DoctorInfo]:
        query = name_or_alias.lower()
        for doc in self.doctors:
            if doc.normalized_name == query:
                return doc
            if any(query in alias for alias in doc.aliases):
                return doc
        return None


@dataclass
class TenantConfig:
    """
    Complete runtime config for one tenant.
    Serialized to/from Redis as JSON.
    """

    tenant_id: str
    slug: str
    name: str
    is_active: bool
    transfer_number: Optional[str]
    default_language: str
    greeting_text: Optional[str]
    fallback_text: Optional[str]
    stt_language_code: Optional[str]
    tts_voice: Optional[str]
    branches: list[BranchInfo] = field(default_factory=list)
    keyword_rules: list[dict] = field(default_factory=list)

    def get_main_branch(self) -> Optional[BranchInfo]:
        for b in self.branches:
            if b.is_main_branch:
                return b
        return self.branches[0] if self.branches else None

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict for Redis storage."""
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TenantConfig":
        """Deserialize from Redis-stored dict."""
        branches = [
            BranchInfo(
                **{
                    **b,
                    "departments": [DepartmentInfo(**d) for d in b.get("departments", [])],
                    "doctors": [DoctorInfo(**doc) for doc in b.get("doctors", [])],
                }
            )
            for b in data.get("branches", [])
        ]
        return cls(
            tenant_id=data["tenant_id"],
            slug=data["slug"],
            name=data["name"],
            is_active=data["is_active"],
            transfer_number=data.get("transfer_number"),
            default_language=data.get("default_language", "ml"),
            greeting_text=data.get("greeting_text"),
            fallback_text=data.get("fallback_text"),
            stt_language_code=data.get("stt_language_code"),
            tts_voice=data.get("tts_voice"),
            branches=branches,
            keyword_rules=data.get("keyword_rules", []),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────


async def load_tenant_config(slug: str) -> Optional[TenantConfig]:
    """
    Load tenant config with cache-first strategy.

    Hot path: cache hit → O(1) Redis get + JSON decode.
    Cold path: DB query → build config → cache for next call.
    """
    # 1. Cache hit
    cached = await get_tenant_config_cache(slug)
    if cached is not None:
        return TenantConfig.from_dict(cached)

    # 2. DB query
    config = await _load_from_db(slug)
    if config is None:
        return None

    # 3. Store in cache
    await set_tenant_config_cache(slug, config.to_dict())
    return config


async def _load_from_db(slug: str) -> Optional[TenantConfig]:
    """
    Full DB load with eager-loaded relationships.
    Called only on cache miss.
    """
    async with get_db_session() as session:
        # Load tenant + keyword rules
        result = await session.execute(
            select(Tenant)
            .where(Tenant.slug == slug, Tenant.is_active == True)
            .options(selectinload(Tenant.keyword_rules))
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            return None

        # Load branches with all related data
        branches_result = await session.execute(
            select(HospitalBranch)
            .where(HospitalBranch.tenant_id == tenant.id)
            .options(
                selectinload(HospitalBranch.departments).options(
                    selectinload(Department.timings),
                    selectinload(Department.fees),
                ),
                selectinload(HospitalBranch.doctors).options(
                    selectinload(Doctor.availability),
                    selectinload(Doctor.fees),
                ),
                selectinload(HospitalBranch.day_policies),
                selectinload(HospitalBranch.holiday_overrides),
            )
        )
        db_branches = branches_result.scalars().all()

        branches: list[BranchInfo] = []
        for b in db_branches:
            departments = [
                DepartmentInfo(
                    id=str(d.id),
                    name=d.name,
                    normalized_name=d.normalized_name,
                    aliases=d.aliases or [],
                    is_active=d.is_active,
                    floor_info=d.floor_info,
                    room_number=d.room_number,
                    timings=[
                        {
                            "day": t.day_of_week.value,
                            "open_time": t.open_time.strftime("%H:%M") if t.open_time else None,
                            "close_time": t.close_time.strftime("%H:%M") if t.close_time else None,
                            "is_closed": t.is_closed,
                            "session_label": t.session_label,
                        }
                        for t in d.timings
                    ],
                    fees=[
                        {
                            "fee_type": f.fee_type,
                            "amount": f.amount,
                            "currency": f.currency,
                            "notes": f.notes,
                        }
                        for f in d.fees
                    ],
                )
                for d in b.departments
            ]

            doctors = [
                DoctorInfo(
                    id=str(doc.id),
                    name=doc.name,
                    normalized_name=doc.normalized_name,
                    aliases=doc.aliases or [],
                    specialization=doc.specialization,
                    department_id=str(doc.department_id) if doc.department_id else None,
                    is_active=doc.is_active,
                    is_visiting=doc.is_visiting,
                    availability=[
                        {
                            "day": a.day_of_week.value,
                            "start_time": a.start_time.strftime("%H:%M") if a.start_time else None,
                            "end_time": a.end_time.strftime("%H:%M") if a.end_time else None,
                            "is_available": a.is_available,
                            "slot_notes": a.slot_notes,
                        }
                        for a in doc.availability
                    ],
                    fees=[
                        {
                            "fee_type": f.fee_type,
                            "amount": f.amount,
                            "currency": f.currency,
                        }
                        for f in doc.fees
                    ],
                )
                for doc in b.doctors
            ]

            branches.append(
                BranchInfo(
                    id=str(b.id),
                    name=b.name,
                    is_main_branch=b.is_main_branch,
                    address=b.address,
                    city=b.city,
                    district=b.district,
                    phone_primary=b.phone_primary,
                    phone_secondary=b.phone_secondary,
                    phone_emergency=b.phone_emergency,
                    has_emergency=b.has_emergency,
                    emergency_24x7=b.emergency_24x7,
                    emergency_notes=b.emergency_notes,
                    general_open_time=(
                        b.general_open_time.strftime("%H:%M") if b.general_open_time else None
                    ),
                    general_close_time=(
                        b.general_close_time.strftime("%H:%M") if b.general_close_time else None
                    ),
                    departments=departments,
                    doctors=doctors,
                    day_policies=[
                        {
                            "day": p.day_of_week.value,
                            "is_open": p.is_open,
                            "open_time": p.open_time.strftime("%H:%M") if p.open_time else None,
                            "close_time": p.close_time.strftime("%H:%M") if p.close_time else None,
                            "notes": p.notes,
                        }
                        for p in b.day_policies
                    ],
                    holiday_overrides=[
                        {
                            "date": h.override_date.strftime("%Y-%m-%d"),
                            "is_closed": h.is_closed,
                            "reason": h.reason,
                            "emergency_only": h.emergency_only,
                        }
                        for h in b.holiday_overrides
                    ],
                )
            )

        return TenantConfig(
            tenant_id=str(tenant.id),
            slug=tenant.slug,
            name=tenant.name,
            is_active=tenant.is_active,
            transfer_number=tenant.transfer_number,
            default_language=tenant.default_language.value
            if hasattr(tenant.default_language, "value")
            else str(tenant.default_language),
            greeting_text=tenant.greeting_text,
            fallback_text=tenant.fallback_text,
            stt_language_code=tenant.stt_language_code,
            tts_voice=tenant.tts_voice,
            branches=branches,
            keyword_rules=[
                {
                    "keyword": kw.keyword,
                    "maps_to_intent": kw.maps_to_intent,
                    "maps_to_entity": kw.maps_to_entity,
                    "weight": kw.weight,
                }
                for kw in tenant.keyword_rules
                if kw.is_active
            ],
        )
