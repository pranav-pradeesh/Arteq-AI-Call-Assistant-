"""
Hospital Knowledge Service.

Retrieves structured answers from the TenantConfig.
No LLM. No guessing. Only facts from the data model.

All lookups are O(1) or O(n) where n is small (< 50 items per branch).
Returns typed KnowledgeResult objects consumed by the response composer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

import pytz

from src.intent.engine import ExtractedEntities, IntentResult
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE,
    INTENT_CONTACT,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_EMERGENCY,
    INTENT_GOODBYE,
    INTENT_HOSPITAL_TIMING,
    INTENT_HUMAN_TRANSFER,
    INTENT_LOCATION,
    INTENT_REPEAT,
    INTENT_UNKNOWN,
)
from src.tenant.loader import BranchInfo, DoctorInfo, TenantConfig

INDIA_TZ = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class KnowledgeResult:
    """
    Structured answer from the knowledge service.
    Consumed by the response composer to phrase a natural reply.
    """

    intent: str
    found: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    missing_entity: Optional[str] = None    # what we couldn't find
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Service
# ─────────────────────────────────────────────────────────────────────────────


class HospitalKnowledgeService:
    """
    Reads from TenantConfig (always up-to-date from cache).
    Stateless — safe to reuse across calls.
    """

    def __init__(self, config: TenantConfig):
        self.config = config

    def answer(
        self,
        intent_result: IntentResult,
        state_context: Optional[Dict] = None,
    ) -> KnowledgeResult:
        """
        Route to the correct handler based on intent.
        Falls back to UNKNOWN if handler not found.
        """
        entities = intent_result.entities

        # Apply conversation context to fill missing entities
        if state_context:
            if not entities.department and state_context.get("last_department"):
                entities.department = state_context["last_department"]
            if not entities.doctor_name and state_context.get("last_doctor_name"):
                entities.doctor_name = state_context["last_doctor_name"]
            if not entities.day_reference and state_context.get("last_day_reference"):
                entities.day_reference = state_context["last_day_reference"]

        branch = self.config.get_main_branch()
        if not branch:
            return KnowledgeResult(
                intent=intent_result.intent,
                found=False,
                error="no_branch_configured",
            )

        handlers = {
            INTENT_DOCTOR_AVAILABILITY: self._doctor_availability,
            INTENT_DOCTOR_TIMING: self._doctor_timing,
            INTENT_CONSULTATION_FEE: self._consultation_fee,
            INTENT_DEPARTMENT_EXISTS: self._department_exists,
            INTENT_HOSPITAL_TIMING: self._hospital_timing,
            INTENT_EMERGENCY: self._emergency,
            INTENT_LOCATION: self._location,
            INTENT_CONTACT: self._contact,
        }

        handler = handlers.get(intent_result.intent)
        if handler:
            return handler(branch, entities)

        return KnowledgeResult(intent=intent_result.intent, found=False)

    # ─── Intent handlers ──────────────────────────────────────────────────────

    def _doctor_availability(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        """Is a doctor (or doctors in a department) available?"""
        today_day = _get_today_day_name(entities.day_reference)

        # Case 1: specific doctor name mentioned
        if entities.doctor_name:
            doc = branch.get_doctor(entities.doctor_name)
            if not doc:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY,
                    found=False,
                    missing_entity="doctor_name",
                    data={"query_name": entities.doctor_name},
                )
            avail = _get_availability_for_day(doc.availability, today_day)
            return KnowledgeResult(
                intent=INTENT_DOCTOR_AVAILABILITY,
                found=True,
                data={
                    "doctor_name": doc.name,
                    "day": today_day,
                    "available": avail is not None and avail.get("is_available", False),
                    "slots": avail,
                    "department": _get_dept_name_by_id(branch, doc.department_id),
                },
            )

        # Case 2: department mentioned — list available doctors
        if entities.department:
            dept = branch.get_department(entities.department)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY,
                    found=False,
                    missing_entity="department",
                    data={"query_dept": entities.department},
                )
            dept_doctors = [
                d for d in branch.doctors
                if d.department_id == dept.id and d.is_active
            ]
            available_today = [
                {
                    "name": d.name,
                    "slots": _get_availability_for_day(d.availability, today_day),
                }
                for d in dept_doctors
                if _get_availability_for_day(d.availability, today_day) is not None
            ]
            return KnowledgeResult(
                intent=INTENT_DOCTOR_AVAILABILITY,
                found=True,
                data={
                    "department": dept.name,
                    "day": today_day,
                    "available_doctors": available_today,
                    "total_doctors": len(dept_doctors),
                },
            )

        # No specific doctor or department — generic response
        return KnowledgeResult(
            intent=INTENT_DOCTOR_AVAILABILITY,
            found=False,
            missing_entity="department_or_doctor",
        )

    def _doctor_timing(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        """OP timing for a department or doctor."""
        today_day = _get_today_day_name(entities.day_reference)

        if entities.department:
            dept = branch.get_department(entities.department)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING,
                    found=False,
                    missing_entity="department",
                )
            timing = _get_dept_timing_for_day(dept.timings, today_day)
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING,
                found=True,
                data={
                    "department": dept.name,
                    "day": today_day,
                    "timing": timing,
                },
            )

        if entities.doctor_name:
            doc = branch.get_doctor(entities.doctor_name)
            if not doc:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING,
                    found=False,
                    missing_entity="doctor_name",
                )
            avail = _get_availability_for_day(doc.availability, today_day)
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING,
                found=True,
                data={
                    "doctor_name": doc.name,
                    "day": today_day,
                    "timing": avail,
                },
            )

        # General hospital OP timing
        timing_data = _get_general_timing(branch, today_day)
        return KnowledgeResult(
            intent=INTENT_DOCTOR_TIMING,
            found=timing_data is not None,
            data=timing_data or {},
        )

    def _consultation_fee(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        """Consultation fee for a department or doctor."""
        fee_type = entities.fee_type or "consultation"

        if entities.doctor_name:
            doc = branch.get_doctor(entities.doctor_name)
            if doc:
                fee = _find_fee(doc.fees, fee_type)
                if fee:
                    return KnowledgeResult(
                        intent=INTENT_CONSULTATION_FEE,
                        found=True,
                        data={
                            "doctor_name": doc.name,
                            "fee_type": fee_type,
                            "amount": fee["amount"],
                            "currency": fee["currency"],
                        },
                    )

        if entities.department:
            dept = branch.get_department(entities.department)
            if dept:
                fee = _find_fee(dept.fees, fee_type)
                if fee:
                    return KnowledgeResult(
                        intent=INTENT_CONSULTATION_FEE,
                        found=True,
                        data={
                            "department": dept.name,
                            "fee_type": fee_type,
                            "amount": fee["amount"],
                            "currency": fee["currency"],
                        },
                    )
                # Dept found but no fee configured
                return KnowledgeResult(
                    intent=INTENT_CONSULTATION_FEE,
                    found=False,
                    missing_entity="fee_not_configured",
                    data={"department": dept.name},
                )

        return KnowledgeResult(
            intent=INTENT_CONSULTATION_FEE,
            found=False,
            missing_entity="department_or_doctor",
        )

    def _department_exists(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        """Does this hospital have a specific department?"""
        if not entities.department:
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS,
                found=False,
                missing_entity="department",
            )

        dept = branch.get_department(entities.department)
        if dept and dept.is_active:
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS,
                found=True,
                data={
                    "department": dept.name,
                    "floor": dept.floor_info,
                    "room": dept.room_number,
                    "doctor_count": len([
                        d for d in branch.doctors
                        if d.department_id == dept.id and d.is_active
                    ]),
                },
            )

        return KnowledgeResult(
            intent=INTENT_DEPARTMENT_EXISTS,
            found=False,
            data={"query_dept": entities.department},
        )

    def _hospital_timing(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        """Is the hospital open? What are the general timings?"""
        today_day = _get_today_day_name(entities.day_reference)
        today_date_str = datetime.now(INDIA_TZ).strftime("%Y-%m-%d")

        # Check holiday overrides first
        holiday = _check_holiday(branch.holiday_overrides, today_date_str)
        if holiday:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING,
                found=True,
                data={
                    "day": today_day,
                    "is_holiday": True,
                    "is_closed": holiday["is_closed"],
                    "reason": holiday.get("reason"),
                    "emergency_only": holiday.get("emergency_only", False),
                },
            )

        # Check day policy
        day_policy = _get_day_policy(branch.day_policies, today_day)
        if day_policy:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING,
                found=True,
                data={
                    "day": today_day,
                    "is_open": day_policy["is_open"],
                    "open_time": day_policy.get("open_time"),
                    "close_time": day_policy.get("close_time"),
                    "notes": day_policy.get("notes"),
                },
            )

        # Fall back to general timings
        general_data = _get_general_timing(branch, today_day)
        return KnowledgeResult(
            intent=INTENT_HOSPITAL_TIMING,
            found=general_data is not None,
            data=general_data or {"day": today_day},
        )

    def _emergency(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        return KnowledgeResult(
            intent=INTENT_EMERGENCY,
            found=True,
            data={
                "has_emergency": branch.has_emergency,
                "emergency_24x7": branch.emergency_24x7,
                "emergency_phone": branch.phone_emergency,
                "notes": branch.emergency_notes,
            },
        )

    def _location(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        has_data = bool(branch.address or branch.city)
        return KnowledgeResult(
            intent=INTENT_LOCATION,
            found=has_data,
            data={
                "name": branch.name,
                "address": branch.address,
                "city": branch.city,
                "district": branch.district,
                "state": "Kerala",
            },
        )

    def _contact(
        self, branch: BranchInfo, entities: ExtractedEntities
    ) -> KnowledgeResult:
        has_data = bool(branch.phone_primary)
        return KnowledgeResult(
            intent=INTENT_CONTACT,
            found=has_data,
            data={
                "phone_primary": branch.phone_primary,
                "phone_secondary": branch.phone_secondary,
                "phone_emergency": branch.phone_emergency,
                "whatsapp": branch.whatsapp,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_today_day_name(day_reference: Optional[str] = None) -> str:
    """Return the day of week name for the reference."""
    if day_reference and day_reference not in ("today", None):
        return day_reference

    now = datetime.now(INDIA_TZ)
    return now.strftime("%A").lower()  # e.g. "monday"


def _get_availability_for_day(
    availability: list, day: str
) -> Optional[dict]:
    """Find availability entry for a given day. O(n), n <= 7."""
    for slot in availability:
        if slot.get("day") == day and slot.get("is_available", True):
            return slot
    return None


def _get_dept_timing_for_day(timings: list, day: str) -> Optional[dict]:
    """Find timing entry for a given day."""
    for t in timings:
        if t.get("day") == day and not t.get("is_closed", False):
            return t
    return None


def _find_fee(fees: list, fee_type: str) -> Optional[dict]:
    """Find first fee entry matching fee_type."""
    for f in fees:
        if f.get("fee_type") == fee_type:
            return f
    return fees[0] if fees else None  # fallback to first fee


def _check_holiday(overrides: list, date_str: str) -> Optional[dict]:
    """Check if today is a holiday. O(n), n is small."""
    for h in overrides:
        if h.get("date") == date_str:
            return h
    return None


def _get_day_policy(policies: list, day: str) -> Optional[dict]:
    """Get branch day policy for a given day."""
    for p in policies:
        if p.get("day") == day:
            return p
    return None


def _get_general_timing(branch: BranchInfo, day: str) -> Optional[dict]:
    """Build general timing response from branch defaults."""
    if not branch.general_open_time:
        return None
    return {
        "day": day,
        "is_open": True,  # if general times exist, assume open
        "open_time": branch.general_open_time,
        "close_time": branch.general_close_time,
    }


def _get_dept_name_by_id(branch: BranchInfo, dept_id: Optional[str]) -> Optional[str]:
    if not dept_id:
        return None
    for d in branch.departments:
        if d.id == dept_id:
            return d.name
    return None
