"""Tests for response composer."""

import pytest
from src.response.composer import ResponseComposer
from src.knowledge.service import KnowledgeResult
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE, INTENT_DOCTOR_AVAILABILITY,
    INTENT_DEPARTMENT_EXISTS, INTENT_EMERGENCY, INTENT_LOCATION,
)


@pytest.fixture
def composer():
    return ResponseComposer(hospital_name="Mother Hospital", use_llm=False)


def test_dept_exists_with_floor(composer):
    result = KnowledgeResult(
        intent=INTENT_DEPARTMENT_EXISTS,
        found=True,
        data={"department": "Dental", "floor": "Ground Floor", "doctor_count": 2},
    )
    text = composer.compose(result)
    assert "Dental" in text
    assert len(text) < 300  # responses must be short


def test_dept_not_exists(composer):
    result = KnowledgeResult(
        intent=INTENT_DEPARTMENT_EXISTS,
        found=False,
        data={"query_dept": "oncology"},
    )
    text = composer.compose(result)
    assert "oncology" in text.lower() or "ക്ഷമിക്കണം" in text


def test_fee_response(composer):
    result = KnowledgeResult(
        intent=INTENT_CONSULTATION_FEE,
        found=True,
        data={"department": "Dental", "fee_type": "consultation", "amount": 200.0, "currency": "INR"},
    )
    text = composer.compose(result)
    assert "200" in text
    assert "INR" in text or "₹" in text


def test_doctor_available(composer):
    result = KnowledgeResult(
        intent=INTENT_DOCTOR_AVAILABILITY,
        found=True,
        data={
            "doctor_name": "Dr. Rema Devi",
            "day": "monday",
            "available": True,
            "slots": {"start_time": "09:00", "end_time": "13:00", "is_available": True},
        },
    )
    text = composer.compose(result)
    assert "Rema Devi" in text
    assert "09:00" in text


def test_doctor_not_available(composer):
    result = KnowledgeResult(
        intent=INTENT_DOCTOR_AVAILABILITY,
        found=True,
        data={"doctor_name": "Dr. Rema Devi", "day": "friday", "available": False, "slots": None},
    )
    text = composer.compose(result)
    assert "ക്ഷമിക്കണം" in text or "available" in text.lower()


def test_emergency_24x7(composer):
    result = KnowledgeResult(
        intent=INTENT_EMERGENCY,
        found=True,
        data={
            "has_emergency": True,
            "emergency_24x7": True,
            "emergency_phone": "0487-2442000",
        },
    )
    text = composer.compose(result)
    assert "24" in text
    assert "0487-2442000" in text


def test_location_response(composer):
    result = KnowledgeResult(
        intent=INTENT_LOCATION,
        found=True,
        data={"address": "Pullazhy, Thrissur", "city": "Thrissur", "district": "Thrissur"},
    )
    text = composer.compose(result)
    assert "Thrissur" in text


def test_clarification_messages(composer):
    c0 = composer.clarification(attempt=0)
    c1 = composer.clarification(attempt=1)
    c2 = composer.clarification(attempt=2)
    assert len(c0) > 10
    assert len(c1) > 10
    # All are Malayalam
    assert any(ord(c) > 0x0D00 for c in c0)


def test_greeting_contains_hospital_name(composer):
    g = composer.greeting()
    assert "Mother Hospital" in g
