"""
Per-tenant feature flags + tier-default matrix.

A tenant's `features` is a JSON object {feature_key: bool}, stored in the
control-DB `tenants` table. When a hospital/clinic is onboarded, defaults are
filled from its tier (hospital = full, clinic = leaner) and are then editable
per tenant from the admin dashboard.

Add a new capability in one place: append to FEATURES, set its tier defaults,
then gate on it with `enabled(features, key)`.
"""
from __future__ import annotations

from typing import Optional

# key -> human label (shown in the dashboard toggle list)
FEATURES: dict[str, str] = {
    "outbound_calls":          "Outbound calls (reminders, confirmations, callbacks, follow-ups)",
    "campaigns":               "Bulk outbound campaigns",
    "phone_sip":               "Phone / SIP telephony (Plivo DID + LiveKit SIP)",
    "his_integration":         "HIS integration (live doctor slots via FHIR/REST)",
    "premium_llm":             "Premium LLM (llama-3.3-70b instead of 8b)",
    "analytics":               "Analytics dashboard (calls, stats)",
    "staff_sms_alerts":        "Staff SMS alerts (booking / cancel / emergency)",
    "multi_department_routing":"Multi-department routing + transfers",
    "dtmf":                    "DTMF keypad menu",
    "patient_recognition":     "Returning-patient recognition (greet by name)",
    "after_hours_callback":    "After-hours callback offer",
    "post_call_sms":           "Post-call summary SMS",
}

# Browser /talk and core conversation are always on — they are not feature-gated.

_HOSPITAL_DEFAULTS = {key: True for key in FEATURES}

_CLINIC_DEFAULTS = {
    "outbound_calls":           True,
    "campaigns":                False,
    "phone_sip":                True,
    "his_integration":          False,
    "premium_llm":              False,   # 8b keeps clinic cost low
    "analytics":                True,
    "staff_sms_alerts":         True,
    "multi_department_routing": False,   # clinics are small / single-dept
    "dtmf":                     True,
    "patient_recognition":      True,
    "after_hours_callback":     True,
    "post_call_sms":            False,
}

TIER_DEFAULTS: dict[str, dict[str, bool]] = {
    "hospital": _HOSPITAL_DEFAULTS,
    "clinic":   _CLINIC_DEFAULTS,
}


def default_features(tier: str) -> dict[str, bool]:
    """Feature map for a tier, with every known key present."""
    base = TIER_DEFAULTS.get(tier, _HOSPITAL_DEFAULTS)
    return {key: bool(base.get(key, False)) for key in FEATURES}


def normalize(features: Optional[dict], tier: str = "hospital") -> dict[str, bool]:
    """Merge stored flags over tier defaults so every known key is present and
    unknown keys are dropped. Tolerates None / partial maps."""
    merged = default_features(tier)
    if features:
        for key, val in features.items():
            if key in FEATURES:
                merged[key] = bool(val)
    return merged


def enabled(features: Optional[dict], key: str) -> bool:
    """True if `key` is on. Unknown tenants/keys default to on for hospital-grade
    safety (a missing flag should not silently disable a core capability)."""
    if not features:
        return True
    return bool(features.get(key, True))
