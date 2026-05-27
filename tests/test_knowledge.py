"""
Knowledge service tests — uses synthetic HospitalContext (no DB needed).

day_of_week DB convention:  0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
"""
import pytest
from unittest.mock import patch

from src.db.queries import (
    BillingRow, DeptInfo, DoctorInfo, EmergencyContact, FaqRow,
    HospitalContext, SlotInfo,
)
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


# ── Synthetic hospital context ────────────────────────────────────────────────

def make_test_context() -> HospitalContext:
    dept_dental = DeptInfo(
        id="d1", name="Dental", name_ml="ഡെന്റൽ",
        floor="Ground Floor", location_hint="", phone_ext="",
    )
    dept_gyno = DeptInfo(
        id="d2", name="Gynaecology", name_ml="ഗൈനക്കോളജി",
        floor="2nd Floor", location_hint="", phone_ext="",
    )
    # Doctor available Monday (dow=1) and Wednesday (dow=3)
    doctor = DoctorInfo(
        id="doc1", name="Dr. Rema Devi", name_ml="ഡോ. രേമ ദേവി",
        specialty="Obstetrics", qualifications="MBBS",
        dept_name="Gynaecology", dept_name_ml="ഗൈനക്കോളജി",
        slots=[
            SlotInfo(dow=1, start="09:00", end="13:00", room="G1"),
            SlotInfo(dow=3, start="09:00", end="13:00", room="G1"),
        ],
    )
    billing = [
        BillingRow(item="consultation:dental", item_ml="ഡെന്റൽ",
                   price_min=200.0, price_max=200.0, notes=""),
        BillingRow(item="consultation:gynaecology", item_ml="ഗൈനക്കോളജി",
                   price_min=300.0, price_max=350.0, notes=""),
    ]
    emergency = [EmergencyContact(label="Emergency", label_ml="ഇമർജൻസി",
                                  phone="0487-2442000")]
    return HospitalContext(
        hospital_id="test-hospital",
        name="Test Hospital",
        name_ml="ടെസ്റ്റ് ഹോസ്പിറ്റൽ",
        address="Pullazhy, Thrissur",
        phone="0487-2442888",
        hours={
            "mon": ["08:00", "20:00"],
            "sun": ["09:00", "14:00"],
        },
        departments=[dept_dental, dept_gyno],
        doctors=[doctor],
        billing=billing,
        faqs=[],
        emergency=emergency,
    )


@pytest.fixture
def service():
    return HospitalKnowledgeService(make_test_context())


# ── Department exists ─────────────────────────────────────────────────────────

def test_department_exists_dentist(service):
    result = service.answer(INTENT_DEPARTMENT_EXISTS, {"department": "dental"})
    assert result.found is True
    assert "Dental" in result.text_ml or "ഡെന്റൽ" in result.text_ml


def test_department_exists_alias(service):
    # "dental" is a substring of "Dental" so find_dept("dental") should match
    result = service.answer(INTENT_DEPARTMENT_EXISTS, {"department": "dental"})
    assert result.found is True


def test_department_not_exists(service):
    result = service.answer(INTENT_DEPARTMENT_EXISTS, {"department": "oncology"})
    assert result.found is False
    assert "ക്ഷമിക്കണം" in result.text_ml


# ── Consultation fee ──────────────────────────────────────────────────────────

def test_fee_by_department(service):
    result = service.answer(INTENT_CONSULTATION_FEE, {"department": "dental"})
    assert result.found is True
    assert "200" in result.text_ml


def test_fee_unknown_department(service):
    result = service.answer(INTENT_CONSULTATION_FEE, {"department": "cardiology"})
    assert result.found is False


# ── Doctor availability ───────────────────────────────────────────────────────

def test_doctor_availability_monday(service):
    # Monday = DB dow 1 — doctor has slot
    with patch("src.knowledge.service.today_db_dow", return_value=1):
        result = service.answer(
            INTENT_DOCTOR_AVAILABILITY, {"doctor_name": "rema devi"}
        )
    assert result.found is True
    assert "09:00" in result.text_ml


def test_doctor_availability_friday(service):
    # Friday = DB dow 5 — doctor has no slot
    with patch("src.knowledge.service.today_db_dow", return_value=5):
        result = service.answer(
            INTENT_DOCTOR_AVAILABILITY, {"doctor_name": "rema"}
        )
    assert result.found is True
    assert "available അല്ല" in result.text_ml


def test_doctor_unknown_name(service):
    result = service.answer(
        INTENT_DOCTOR_AVAILABILITY, {"doctor_name": "dr nobody"}
    )
    assert result.found is False
    assert "ക്ഷമിക്കണം" in result.text_ml


# ── Emergency ─────────────────────────────────────────────────────────────────

def test_emergency_available(service):
    result = service.answer(INTENT_EMERGENCY, {})
    assert result.found is True
    assert "0487-2442000" in result.text_ml


# ── Location ──────────────────────────────────────────────────────────────────

def test_location(service):
    result = service.answer(INTENT_LOCATION, {})
    assert result.found is True
    assert "Thrissur" in result.text_ml


# ── Hospital timing ───────────────────────────────────────────────────────────

def test_hospital_timing_today(service):
    # Use Monday (dow=1) — hospital is open
    with patch("src.knowledge.service.today_db_dow", return_value=1):
        result = service.answer(INTENT_HOSPITAL_TIMING, {})
    assert result.found is True
    assert "08:00" in result.text_ml


# ── Context carryover ─────────────────────────────────────────────────────────

def test_context_department_carryover(service):
    """If no department in entities, should fall back to state_context."""
    result = service.answer(
        INTENT_CONSULTATION_FEE,
        {},  # no department in entities
        state_context={"last_department": "dental"},
    )
    assert result.found is True
    assert "200" in result.text_ml
