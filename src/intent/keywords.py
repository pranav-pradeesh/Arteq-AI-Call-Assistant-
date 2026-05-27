"""
Malayalam/Manglish keyword dictionary for intent and entity detection.

Design:
  - Each intent has a weighted keyword list
  - Keywords include standard Malayalam, Manglish, and common variants
  - Department synonyms are separate for entity extraction
  - Weights are higher for unambiguous keywords
  - Scoring is O(k) where k = number of tokens in transcript

This is the single source of truth for intent vocabulary.
Tenant-level overrides (from KeywordRule table) are merged at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Intent constants
# ─────────────────────────────────────────────────────────────────────────────

INTENT_DOCTOR_AVAILABILITY = "doctor_availability"
INTENT_DOCTOR_TIMING = "doctor_timing"
INTENT_CONSULTATION_FEE = "consultation_fee"
INTENT_DEPARTMENT_EXISTS = "department_exists"
INTENT_HOSPITAL_TIMING = "hospital_timing"
INTENT_EMERGENCY = "emergency_availability"
INTENT_LOCATION = "location_query"
INTENT_CONTACT = "contact_query"
INTENT_HUMAN_TRANSFER = "human_transfer"
INTENT_REPEAT = "repeat_request"
INTENT_GOODBYE = "goodbye"
INTENT_UNKNOWN = "unknown"

ALL_INTENTS = [
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_CONSULTATION_FEE,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_HOSPITAL_TIMING,
    INTENT_EMERGENCY,
    INTENT_LOCATION,
    INTENT_CONTACT,
    INTENT_HUMAN_TRANSFER,
    INTENT_REPEAT,
    INTENT_GOODBYE,
    INTENT_UNKNOWN,
]


# ─────────────────────────────────────────────────────────────────────────────
# Keyword → intent mapping
# Format: (keyword, weight)  —  weight 1.0 = baseline, 2.0 = strong signal
# ─────────────────────────────────────────────────────────────────────────────

INTENT_KEYWORDS: Dict[str, List[Tuple[str, float]]] = {

    # ── Doctor availability ──────────────────────────────────────────────────
    INTENT_DOCTOR_AVAILABILITY: [
        # Malayalam
        ("doctor", 1.2),
        ("docter", 1.0),          # common misspelling in Manglish
        ("doctar", 0.9),
        ("vaidyan", 1.5),         # Malayalam for doctor
        ("chikitsan", 1.2),       # doctor/physician
        ("available", 1.5),
        ("undo", 1.3),            # "undo?" = "is there?" in Malayalam
        ("und", 1.0),
        ("undu", 1.2),
        ("undakum", 1.0),         # "will be there"
        ("varum", 1.0),           # "will come"
        ("varu", 0.9),
        ("vann", 0.9),            # "came"
        ("irikkum", 1.0),         # "will be"
        ("irikkunundo", 1.5),     # "is he/she there?"
        ("annu", 0.8),            # "today"
        ("innu", 1.0),            # "today"
        ("innale", 0.7),          # "yesterday"
        ("nale", 0.9),            # "tomorrow"
        ("consult", 1.2),
        ("consultation", 1.2),
        ("op", 1.0),              # outpatient (also timing)
        ("opd", 1.2),
    ],

    # ── Doctor / Department timing ───────────────────────────────────────────
    INTENT_DOCTOR_TIMING: [
        ("time", 1.5),
        ("timing", 2.0),
        ("timings", 2.0),
        ("samayam", 2.0),         # time in Malayalam
        ("samaym", 1.5),          # common mispronunciation
        ("eppo", 1.8),            # "when" in Malayalam
        ("eppozha", 1.8),
        ("eppol", 1.8),
        ("epozhanu", 1.5),
        ("ethra", 1.0),           # "how much/when"
        ("mathiyo", 1.0),         # "is it ok?" (approximate)
        ("morning", 1.0),
        ("evening", 1.0),
        ("noon", 0.8),
        ("night", 0.8),
        ("rathri", 1.0),          # night
        ("uduppu", 0.7),
        ("vare", 0.8),            # "till"
        ("mute", 0.8),            # "from" (as in "from 10 till 1")
        ("op", 1.2),              # OP timing specifically
        ("opd", 1.2),
        ("schedule", 1.5),
        ("slots", 1.2),
        ("slot", 1.2),
        ("session", 1.0),
    ],

    # ── Consultation fee ─────────────────────────────────────────────────────
    INTENT_CONSULTATION_FEE: [
        ("fee", 2.0),
        ("fees", 2.0),
        ("charge", 2.0),
        ("charges", 2.0),
        ("rate", 1.5),
        ("rates", 1.5),
        ("cost", 1.5),
        ("amount", 1.5),
        ("cash", 1.2),
        ("money", 1.2),
        ("pay", 1.2),
        ("payment", 1.2),
        ("consultation fee", 3.0),  # bigram — very strong signal
        ("varadakshina", 2.0),    # Malayalam for consultation fee
        ("chellam", 1.0),         # informal Malayalam for money
        ("panam", 1.8),           # money in Malayalam
        ("ethra", 1.2),           # "how much"
        ("ethraya", 1.5),         # "how much is it"
        ("kada", 0.8),            # colloquial
        ("rupee", 1.2),
        ("rupees", 1.2),
        ("rs", 1.0),
    ],

    # ── Department existence ─────────────────────────────────────────────────
    INTENT_DEPARTMENT_EXISTS: [
        ("department", 1.5),
        ("dept", 1.2),
        ("undakum", 1.0),
        ("undo", 1.2),
        ("und", 0.9),
        ("undu", 1.2),
        ("available", 1.2),
        ("section", 1.0),
        ("vibhagam", 1.5),        # department in Malayalam
        ("speciality", 1.2),
        ("specialty", 1.2),
        ("treat", 1.0),           # "do you treat X"
        ("chikitsa", 1.5),        # treatment/department
    ],

    # ── Hospital timing (general open/close) ─────────────────────────────────
    INTENT_HOSPITAL_TIMING: [
        ("hospital", 1.2),
        ("thirakkum", 1.5),       # "opens" in Malayalam
        ("thurakkunnu", 1.5),
        ("open", 1.5),
        ("opens", 1.5),
        ("close", 1.5),
        ("closes", 1.5),
        ("adakkunnu", 1.5),       # "closes"
        ("adakkum", 1.5),
        ("sunday", 2.0),          # common weekend query
        ("saturday", 1.8),
        ("holiday", 2.0),
        ("bandh", 1.5),
        ("avadhi", 1.5),          # holiday in Malayalam
        ("avasaram", 1.0),        # break/holiday
        ("weekly", 1.5),
        ("weekend", 2.0),
        ("njayar", 2.0),          # Sunday in Malayalam
        ("njayarazcha", 2.0),
        ("saniazcha", 1.8),       # Saturday in Malayalam
        ("saniyazcha", 1.8),
        ("budhanazhcha", 1.0),    # Wednesday
        ("working", 1.5),
        ("off", 1.2),
        ("closed", 1.8),
    ],

    # ── Emergency ────────────────────────────────────────────────────────────
    INTENT_EMERGENCY: [
        ("emergency", 2.5),
        ("emergancy", 2.0),       # common misspelling
        ("urgent", 2.0),
        ("accident", 2.0),
        ("casualty", 2.0),
        ("icu", 2.0),
        ("trauma", 1.8),
        ("ambulance", 2.0),
        ("24", 1.5),
        ("24x7", 2.0),
        ("24hours", 2.0),
        ("night", 1.0),           # ambiguous — lower weight
        ("rathri", 0.8),
        ("athyavasyam", 2.0),     # urgent/emergency in Malayalam
        ("parisrama", 1.0),
        ("prasavam", 1.5),        # delivery/childbirth (emergency adjacent)
        ("heart", 1.8),
        ("chest", 1.2),
        ("breathing", 1.5),
        ("blood", 1.2),
    ],

    # ── Location ─────────────────────────────────────────────────────────────
    INTENT_LOCATION: [
        ("address", 2.0),
        ("location", 2.0),
        ("located", 1.8),         # "where is located"
        ("where", 1.8),
        ("how to reach", 2.0),
        ("how to come", 1.8),
        ("ethi", 1.5),            # "where is" (Malayalam)
        ("evide", 2.0),           # "where" in Malayalam
        ("evideyanu", 2.0),
        ("varthamanathil", 0.8),
        ("map", 1.5),
        ("google", 1.2),
        ("way", 1.2),
        ("road", 1.0),
        ("route", 1.2),
        ("varo", 1.2),            # "how to come"
        ("varuvaan", 1.2),
        ("naattu", 0.8),
        ("district", 1.0),
        ("near", 1.0),
        ("landmark", 1.2),
        ("pincode", 1.0),
        ("pin", 0.8),
    ],

    # ── Contact ──────────────────────────────────────────────────────────────
    INTENT_CONTACT: [
        ("number", 1.8),
        ("phone", 1.8),
        ("contact", 2.0),
        ("call", 1.5),
        ("telephone", 1.5),
        ("mobile", 1.2),
        ("ethelpanikku", 1.5),    # "which number to call"
        ("direct", 1.0),
        ("whatsapp", 1.5),
        ("reception", 1.8),
        ("helpline", 2.0),
        ("hotline", 2.0),
    ],

    # ── Human transfer ───────────────────────────────────────────────────────
    INTENT_HUMAN_TRANSFER: [
        ("staff", 2.0),
        ("nurse", 1.8),
        ("receptionist", 2.0),
        ("human", 2.0),
        ("person", 1.5),
        ("transfer", 2.0),
        ("connect", 1.8),
        ("talk to", 2.0),
        ("speak to", 2.0),
        ("oru aaline", 2.0),       # "a person" in Malayalam
        ("aalkku", 1.5),
        ("aale", 1.5),
        ("aalu", 1.5),
        ("operator", 2.0),
    ],

    # ── Repeat ───────────────────────────────────────────────────────────────
    INTENT_REPEAT: [
        ("repeat", 2.0),
        ("again", 1.8),
        ("paranju", 1.5),         # "say again" in Malayalam
        ("onnum", 1.0),
        ("veedum", 1.5),          # "again"
        ("vendiyum", 1.5),        # "again"
        ("again paranjalo", 1.8),
        ("keto", 1.5),            # "heard?" / colloquial for repeat
        ("ketto", 1.5),
        ("clear", 1.2),
        ("understand", 1.2),
        ("manasilayi", 1.5),      # "understood" (confirming?)
        ("manassilaayilla", 1.8), # "didn't understand"
    ],

    # ── Goodbye ──────────────────────────────────────────────────────────────
    INTENT_GOODBYE: [
        ("bye", 2.0),
        ("goodbye", 2.0),
        ("thank you", 1.8),
        ("thanks", 1.5),
        ("nanni", 1.5),           # "thank you" in Malayalam
        ("okay bye", 2.0),
        ("ok bye", 2.0),
        ("ayi", 1.2),             # "done/over" informal
        ("sheriyayi", 1.5),       # "ok/fine" — call ending signal
        ("end", 1.5),
        ("cut", 1.2),
        ("finish", 1.2),
        ("done", 1.2),
        ("kazhinju", 1.5),        # "done/finished"
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Department entity synonyms
# Used for entity extraction after intent is known
# ─────────────────────────────────────────────────────────────────────────────

DEPARTMENT_SYNONYMS: Dict[str, List[str]] = {
    "dentist": [
        "dentist", "dental", "tooth", "teeth", "pallu", "pallinu",
        "palldoctor", "orthodontic", "orthodontist", "danthachikitsa",
        "dantham", "palluvaidyan",
    ],
    "orthopedic": [
        "orthopedic", "ortho", "bone", "ellu", "ellinu", "joint",
        "spine", "fracture", "fracchar",
    ],
    "cardiology": [
        "cardiology", "cardio", "heart", "hrudayam", "hridayam",
        "hridayarogi", "cardiac",
    ],
    "ent": [
        "ent", "ear", "nose", "throat", "kaan", "mookk", "thallu",
        "ear nose throat", "otolaryngology",
    ],
    "gynecology": [
        "gynecology", "gynecologist", "gynaecology", "obs", "obstetrics",
        "delivery", "prasavam", "strirog", "streerog", "women",
        "maternity",
    ],
    "ophthalmology": [
        "ophthalmology", "eye", "kannu", "kanninu", "ophthal",
        "vision", "eye doctor", "nethram",
    ],
    "pediatrics": [
        "pediatrics", "paediatrics", "child", "kutta", "kuttinu",
        "baby", "balan", "balacikitsa", "kids",
    ],
    "dermatology": [
        "dermatology", "skin", "charma", "charmam", "rog",
        "derma", "skincare",
    ],
    "neurology": [
        "neurology", "neuro", "brain", "thalach", "nerve",
        "stroke", "epilepsy",
    ],
    "general": [
        "general", "gp", "general medicine", "general physician",
        "samanya", "fever", "pani", "penam",
    ],
    "psychiatry": [
        "psychiatry", "mental", "psychology", "manassastra",
        "depression", "anxiety",
    ],
    "urology": [
        "urology", "kidney", "mutra", "mootram", "bladder",
    ],
    "gastroenterology": [
        "gastro", "stomach", "vayar", "digestive", "liver",
        "kalal", "intestine",
    ],
    "pulmonology": [
        "pulmonology", "lung", "chest", "respiration", "asthma",
        "breathing",
    ],
    "oncology": [
        "oncology", "cancer", "tumor", "arbudham",
    ],
    "radiology": [
        "radiology", "xray", "x-ray", "scan", "mri", "ct", "ct scan",
        "ultrasound",
    ],
    "physiotherapy": [
        "physiotherapy", "physio", "rehabilitation", "rehab",
        "exercise",
    ],
    "emergency": [
        "emergency", "casualty", "accident", "urgent",
    ],
    "icu": [
        "icu", "intensive care", "critical",
    ],
    "lab": [
        "lab", "laboratory", "blood test", "test", "pathology",
        "parikshanam",
    ],
    "pharmacy": [
        "pharmacy", "medical", "medicine", "mrundu", "marundhu",
        "drug", "chemist",
    ],
}


# Build reverse lookup: keyword → canonical department name
# O(1) lookup after build
_DEPT_KEYWORD_INDEX: Dict[str, str] = {}

for _dept, _keywords in DEPARTMENT_SYNONYMS.items():
    for _kw in _keywords:
        _DEPT_KEYWORD_INDEX[_kw.lower()] = _dept


def resolve_department(text: str) -> Optional[str]:
    """
    Map a text token to a canonical department name.
    Returns None if no match found.
    O(1) lookup.
    """
    return _DEPT_KEYWORD_INDEX.get(text.lower())


def get_department_synonyms(dept: str) -> List[str]:
    """Get all synonyms for a canonical department name."""
    return DEPARTMENT_SYNONYMS.get(dept.lower(), [])


# ─────────────────────────────────────────────────────────────────────────────
# Day reference mapping
# ─────────────────────────────────────────────────────────────────────────────

DAY_KEYWORDS: Dict[str, str] = {
    # English
    "monday": "monday",
    "tuesday": "tuesday",
    "wednesday": "wednesday",
    "thursday": "thursday",
    "friday": "friday",
    "saturday": "saturday",
    "sunday": "sunday",
    "today": "today",
    "tomorrow": "tomorrow",
    "yesterday": "yesterday",
    # Malayalam
    "njayarazcha": "sunday",
    "njayar": "sunday",
    "thinkal": "monday",
    "thinkalazcha": "monday",
    "chowwa": "tuesday",
    "chowwaazcha": "tuesday",
    "budhanazhcha": "wednesday",
    "budhan": "wednesday",
    "vyazham": "thursday",
    "vyaazhazhcha": "thursday",
    "velli": "friday",
    "velliazcha": "friday",
    "saniazcha": "saturday",
    "saniyazcha": "saturday",
    "sanji": "saturday",
    "innu": "today",
    "innale": "yesterday",
    "nale": "tomorrow",
}


def resolve_day(text: str) -> Optional[str]:
    """Map a day keyword to a normalized day name."""
    return DAY_KEYWORDS.get(text.lower())
