"""
Booking priority.

A single, predictable scoring function so the queue order is explainable to
hospital staff ("emergency first, then seniors, then booking order"). Higher
score = seen earlier. Ties fall back to booking time (handled by the caller's
ORDER BY created_at), so two equal-priority patients keep first-come-first-served.
"""
from __future__ import annotations

import re

EMERGENCY = 1000
SENIOR = 100
SENIOR_AGE = 60

# Words a caller might use that signal urgency. Kept small and literal — true
# triage stays with clinical staff; this only nudges queue order.
_URGENT_WORDS = (
    "emergency", "urgent", "severe", "chest pain", "bleeding", "accident",
    "അത്യാഹിതം", "അടിയന്തിര", "അത്യാവശ്യം",
)


def compute_priority(
    *,
    age: int | None = None,
    is_emergency: bool = False,
    notes: str = "",
) -> int:
    """Return a queue-priority bump for an appointment. 0 = normal."""
    score = 0
    note_l = (notes or "").lower()
    if is_emergency or any(w in note_l for w in _URGENT_WORDS):
        score += EMERGENCY
    if age is not None and age >= SENIOR_AGE:
        score += SENIOR
    return score


def extract_age(text: str) -> int | None:
    """Best-effort age pull from free text like 'age 72' / '72 years' / '72 വയസ്സ്'.
    Returns None when no plausible age (1–120) is found."""
    for m in re.finditer(r"\b(\d{1,3})\b", text or ""):
        n = int(m.group(1))
        if 1 <= n <= 120:
            return n
    return None
