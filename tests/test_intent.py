"""
Intent engine tests covering all scenario types from the spec.

Tests: clean speech, mixed speech, dialect variation, noisy/partial speech,
ambiguous keywords, multiple intents, low confidence.
"""

import pytest

from src.intent.engine import classify_intent
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_EMERGENCY,
    INTENT_GOODBYE,
    INTENT_HOSPITAL_TIMING,
    INTENT_HUMAN_TRANSFER,
    INTENT_LOCATION,
    INTENT_UNKNOWN,
)


# ─── A. Clean speech ──────────────────────────────────────────────────────────


def test_clean_doctor_availability_english():
    result = classify_intent("Is the doctor available today")
    assert result.intent == INTENT_DOCTOR_AVAILABILITY
    assert result.confidence > 0.5


def test_clean_timing_query():
    result = classify_intent("What time does OP start")
    assert result.intent in (INTENT_DOCTOR_TIMING, INTENT_HOSPITAL_TIMING)
    assert result.confidence > 0.4


def test_clean_fee_query():
    result = classify_intent("What is the consultation fee")
    assert result.intent == INTENT_CONSULTATION_FEE
    assert result.confidence > 0.6


def test_clean_emergency():
    result = classify_intent("Is there emergency service available")
    assert result.intent == INTENT_EMERGENCY
    # "available" also scores doctor_availability; new formula penalises that spread.
    # Confidence is lower but intent is correct and above the 0.50 threshold.
    assert result.confidence > 0.55


def test_clean_location():
    result = classify_intent("Where is the hospital located")
    assert result.intent == INTENT_LOCATION
    assert result.confidence > 0.6


def test_clean_contact():
    result = classify_intent("What is the phone number")
    assert result.intent in (INTENT_LOCATION, "contact_query")


def test_clean_goodbye():
    result = classify_intent("Thank you bye")
    assert result.intent == INTENT_GOODBYE


# ─── B. Mixed Malayalam-English speech ───────────────────────────────────────


def test_mixed_doctor_timing():
    result = classify_intent("Doctor-nte timing enthu aanu")  # Manglish
    assert result.intent in (INTENT_DOCTOR_TIMING, INTENT_DOCTOR_AVAILABILITY)


def test_mixed_fee_query():
    result = classify_intent("Consultation fee ethraya")  # mixed
    assert result.intent == INTENT_CONSULTATION_FEE
    assert result.confidence > 0.5


def test_mixed_emergency():
    result = classify_intent("Emergency undakum ithil")  # mixed
    assert result.intent == INTENT_EMERGENCY


def test_mixed_department():
    result = classify_intent("Dentist department undakum")
    assert result.intent in (INTENT_DEPARTMENT_EXISTS, INTENT_DOCTOR_AVAILABILITY)
    assert result.entities.department == "dentist"


# ─── C. Malayalam keywords ────────────────────────────────────────────────────


def test_malayalam_timing_eppo():
    result = classify_intent("OP eppo thudannu")  # "when does OP start"
    assert result.intent in (INTENT_DOCTOR_TIMING, INTENT_HOSPITAL_TIMING)


def test_malayalam_doctor_undo():
    result = classify_intent("Doctor undo innu")  # "is doctor there today"
    assert result.intent == INTENT_DOCTOR_AVAILABILITY


def test_malayalam_fee_panam():
    result = classify_intent("Ethraya panam vendum")  # "how much money needed"
    assert result.intent == INTENT_CONSULTATION_FEE


def test_malayalam_holiday_sunday():
    result = classify_intent("Njayar hospital open aano")  # "is hospital open Sunday"
    assert result.intent == INTENT_HOSPITAL_TIMING
    assert result.entities.day_reference == "sunday"


def test_malayalam_transfer_aale():
    result = classify_intent("Oru aaline talk cheyyan patumo")  # "can I talk to someone"
    assert result.intent == INTENT_HUMAN_TRANSFER


# ─── D. Short / noisy speech fragments ──────────────────────────────────────


def test_single_word_emergency():
    result = classify_intent("emergency")
    assert result.intent == INTENT_EMERGENCY
    assert result.confidence > 0.6


def test_single_word_fee():
    result = classify_intent("fee")
    assert result.intent == INTENT_CONSULTATION_FEE


def test_partial_timing():
    result = classify_intent("timing", partial=True)
    assert result.intent in (INTENT_DOCTOR_TIMING, INTENT_HOSPITAL_TIMING)


def test_very_short_unclear():
    result = classify_intent("um")
    assert result.needs_clarification is True


def test_empty_transcript():
    result = classify_intent("")
    assert result.intent == INTENT_UNKNOWN
    assert result.needs_clarification is True


# ─── E. Entity extraction ─────────────────────────────────────────────────────


def test_entity_dental():
    result = classify_intent("Is there a dentist available")
    assert result.entities.department == "dentist"


def test_entity_tooth():
    result = classify_intent("pallu vaidyan undo")  # tooth doctor
    assert result.entities.department == "dentist"


def test_entity_gynecology():
    result = classify_intent("gynecology department timing")
    assert result.entities.department == "gynecology"


def test_entity_day_today():
    result = classify_intent("innu doctor available aano")  # today doctor available?
    assert result.entities.day_reference == "today"


def test_entity_day_sunday():
    result = classify_intent("Sunday timing enthu")
    assert result.entities.day_reference == "sunday"


def test_entity_day_malayalam():
    result = classify_intent("njayarazcha hospital thirakkumo")  # Sunday hospital opens?
    assert result.entities.day_reference == "sunday"


# ─── F. Context (prior intent) ────────────────────────────────────────────────


def test_context_fee_after_doctor():
    # After knowing about doctor, fee question should still classify correctly
    result = classify_intent("ethraya", prior_intent=INTENT_DOCTOR_AVAILABILITY)
    assert result.intent == INTENT_CONSULTATION_FEE


def test_context_timing_boost():
    result = classify_intent("eppo", prior_intent=INTENT_DOCTOR_AVAILABILITY)
    assert result.intent in (INTENT_DOCTOR_TIMING, INTENT_HOSPITAL_TIMING)


# ─── G. Tenant keyword overrides ─────────────────────────────────────────────


def test_custom_keyword_rule():
    custom_rules = [
        {"keyword": "visarip", "maps_to_intent": "location_query", "maps_to_entity": None, "weight": 2.0}
    ]
    result = classify_intent("visarip enthu", tenant_keyword_rules=custom_rules)
    assert result.intent == INTENT_LOCATION


# ─── H. Multiple signals ─────────────────────────────────────────────────────


def test_dominant_intent_wins():
    # Fee should win when there are both fee and timing signals
    result = classify_intent("consultation fee timing ethraya rupees")
    # Fee signals are stronger with "rupees" and "fee"
    assert result.intent == INTENT_CONSULTATION_FEE


def test_department_detection_heart():
    result = classify_intent("heart doctor available")
    assert result.intent == INTENT_DOCTOR_AVAILABILITY
    assert result.entities.department == "cardiology"


def test_emergency_signal_strong():
    result = classify_intent("accident emergency ambulance")
    assert result.intent == INTENT_EMERGENCY
    assert result.confidence > 0.7
    assert result.entities.is_emergency is True
