"""
Knowledge service tests.
Uses synthetic TenantConfig — no DB needed.
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from src.intent.engine import ExtractedEntities, IntentResult
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_EMERGENCY,
    INTENT_HOSPITAL_TIMING,
    INTENT_LOCATION,
)
from src.knowledge.service import HospitalKnowledgeService
from src.tenant.loader import BranchInfo, DepartmentInfo, DoctorInfo, TenantConfig


def make_test_config() -> TenantConfig:
    """Create a minimal test TenantConfig."""
    dept_dentist = DepartmentInfo(
        id="dept-dentist-id",
        name="Dental",
        normalized_name="dentist",
        aliases=["dental", "tooth", "pallu"],
        is_active=True,
        floor_info="Ground Floor",
        room_number="D1",
        timings=[
            {"day": "monday", "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            {"day": "tuesday", "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            {"day": "sunday", "open_time": None, "close_time": None, "is_closed": True},
        ],
        fees=[{"fee_type": "consultation", "amount": 200.0, "currency": "INR"}],
    )

    dept_gyno = DepartmentInfo(
        id="dept-gyno-id",
        name="Gynaecology",
        normalized_name="gynecology",
        aliases=["gynecology", "obs", "delivery", "prasavam"],
        is_active=True,
        floor_info="2nd Floor",
        room_number=None,
        timings=[
            {"day": "monday", "open_time": "09:00", "close_time": "16:00", "is_closed": False},
        ],
        fees=[{"fee_type": "consultation", "amount": 300.0, "currency": "INR"}],
    )

    doctor = DoctorInfo(
        id="doc-id-1",
        name="Dr. Rema Devi",
        normalized_name="dr rema devi",
        aliases=["rema", "rema devi"],
        specialization="Obstetrics & Gynaecology",
        department_id="dept-gyno-id",
        is_active=True,
        is_visiting=False,
        availability=[
            {"day": "monday", "start_time": "09:00", "end_time": "13:00", "is_available": True},
            {"day": "wednesday", "start_time": "09:00", "end_time": "13:00", "is_available": True},
        ],
        fees=[{"fee_type": "consultation", "amount": 350.0, "currency": "INR"}],
    )

    branch = BranchInfo(
        id="branch-id-1",
        name="Mother Hospital Main",
        is_main_branch=True,
        address="Pullazhy, Thrissur",
        city="Thrissur",
        district="Thrissur",
        phone_primary="0487-2442888",
        phone_secondary="0487-2443999",
        phone_emergency="0487-2442000",
        has_emergency=True,
        emergency_24x7=True,
        emergency_notes="24x7 Casualty",
        general_open_time="08:00",
        general_close_time="20:00",
        departments=[dept_dentist, dept_gyno],
        doctors=[doctor],
        day_policies=[
            {"day": "sunday", "is_open": True, "open_time": "09:00", "close_time": "14:00", "notes": "Limited OPD"},
        ],
        holiday_overrides=[],
    )

    return TenantConfig(
        tenant_id="tenant-id-1",
        slug="mother-hospital-thrissur",
        name="Mother Hospital Thrissur",
        is_active=True,
        transfer_number="0487-2442888",
        default_language="ml",
        greeting_text=None,
        fallback_text=None,
        stt_language_code="ml-IN",
        tts_voice="anushka",
        branches=[branch],
        keyword_rules=[],
    )


def make_intent_result(
    intent: str,
    department: str = None,
    doctor_name: str = None,
    day: str = None,
    fee_type: str = None,
    confidence: float = 0.9,
) -> IntentResult:
    entities = ExtractedEntities(
        department=department,
        doctor_name=doctor_name,
        day_reference=day,
        fee_type=fee_type,
    )
    return IntentResult(
        intent=intent,
        confidence=confidence,
        needs_clarification=False,
        entities=entities,
    )


@pytest.fixture
def service():
    config = make_test_config()
    return HospitalKnowledgeService(config)


# ─── Department exists ────────────────────────────────────────────────────────


def test_department_exists_dentist(service):
    result = service.answer(make_intent_result(INTENT_DEPARTMENT_EXISTS, department="dentist"))
    assert result.found is True
    assert result.data["department"] == "Dental"


def test_department_exists_alias(service):
    result = service.answer(make_intent_result(INTENT_DEPARTMENT_EXISTS, department="pallu"))
    assert result.found is True  # alias should match


def test_department_not_exists(service):
    result = service.answer(make_intent_result(INTENT_DEPARTMENT_EXISTS, department="oncology"))
    assert result.found is False


# ─── Consultation fee ─────────────────────────────────────────────────────────


def test_fee_by_department(service):
    result = service.answer(make_intent_result(INTENT_CONSULTATION_FEE, department="dentist"))
    assert result.found is True
    assert result.data["amount"] == 200.0
    assert result.data["currency"] == "INR"


def test_fee_by_doctor(service):
    result = service.answer(
        make_intent_result(INTENT_CONSULTATION_FEE, doctor_name="rema devi")
    )
    assert result.found is True
    assert result.data["amount"] == 350.0


def test_fee_unknown_department(service):
    result = service.answer(make_intent_result(INTENT_CONSULTATION_FEE, department="cardiology"))
    assert result.found is False


# ─── Doctor availability ──────────────────────────────────────────────────────


def test_doctor_availability_by_name_available(service):
    with patch("src.knowledge.service._get_today_day_name", return_value="monday"):
        result = service.answer(
            make_intent_result(INTENT_DOCTOR_AVAILABILITY, doctor_name="rema devi")
        )
    assert result.found is True
    assert result.data["available"] is True


def test_doctor_availability_by_name_not_available(service):
    with patch("src.knowledge.service._get_today_day_name", return_value="friday"):
        result = service.answer(
            make_intent_result(INTENT_DOCTOR_AVAILABILITY, doctor_name="rema devi")
        )
    assert result.found is True
    assert result.data["available"] is False


def test_doctor_unknown_name(service):
    result = service.answer(
        make_intent_result(INTENT_DOCTOR_AVAILABILITY, doctor_name="dr nobody")
    )
    assert result.found is False
    assert result.missing_entity == "doctor_name"


# ─── Emergency ───────────────────────────────────────────────────────────────


def test_emergency_available(service):
    result = service.answer(make_intent_result(INTENT_EMERGENCY))
    assert result.found is True
    assert result.data["has_emergency"] is True
    assert result.data["emergency_24x7"] is True
    assert result.data["emergency_phone"] == "0487-2442000"


# ─── Location ────────────────────────────────────────────────────────────────


def test_location(service):
    result = service.answer(make_intent_result(INTENT_LOCATION))
    assert result.found is True
    assert "Thrissur" in result.data["address"]


# ─── Hospital timing ─────────────────────────────────────────────────────────


def test_hospital_timing_sunday(service):
    with patch("src.knowledge.service._get_today_day_name", return_value="sunday"):
        result = service.answer(make_intent_result(INTENT_HOSPITAL_TIMING, day="sunday"))
    assert result.found is True
    assert result.data.get("is_open") is True


# ─── Context carryover ────────────────────────────────────────────────────────


def test_context_department_carryover(service):
    """If no department in entities, use context from prior turn."""
    intent_result = make_intent_result(INTENT_CONSULTATION_FEE)  # no department
    context = {"last_department": "dentist"}
    result = service.answer(intent_result, state_context=context)
    assert result.found is True
    assert result.data["amount"] == 200.0
