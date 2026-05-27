"""
Response Composer.

Strategy:
  1. For well-known patterns, use pre-defined Malayalam templates (zero latency).
  2. For complex or edge cases, use Claude Haiku to phrase naturally.
  3. Always keep responses short — max 2 sentences for voice.

Template variables use {bracket} notation.
LLM is only called when templates are insufficient.
"""

from __future__ import annotations

import time
from typing import Optional

import anthropic

from src.config.settings import settings
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
from src.knowledge.service import KnowledgeResult

# ─────────────────────────────────────────────────────────────────────────────
# Clarification messages (Malayalam)
# ─────────────────────────────────────────────────────────────────────────────

CLARIFICATION_MSGS = [
    "ക്ഷമിക്കണം, ശരിയായി കേൾക്കാനായില്ല. ഒന്ന് വീണ്ടും പറയാമോ?",
    "Sorry, clear ആയി കേൾക്കാൻ കഴിഞ്ഞില്ല. വീണ്ടും പറയാമോ?",
    "ഞാൻ ശരിയായി മനസ്സിലാക്കിയില്ല. ഒന്ന് repeat ചെയ്യാമോ?",
]

TRANSFER_MSG = "ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. ഒരു നിമിഷം."
TRANSFER_FAILED_MSG = "ഒരു staff member-ലേക്ക് connect ചെയ്യാൻ ശ്രമിക്കുന്നു. ദയവായി കുറച്ച് സമയം കൂടി call-ൽ ഇരിക്കൂ."

GOODBYE_MSG = "നന്ദി. ആരോഗ്യം നിങ്ങൾക്ക് ഉണ്ടാകട്ടെ. ശുഭദിനം."
FALLBACK_MSG = "ക്ഷമിക്കണം, ഇപ്പോൾ ഞാൻ ഉചിതമായ ഒരു ഉത്തരം നൽകാൻ കഴിയുന്നില്ല. Staff-നോട് ബന്ധപ്പെടാൻ ശ്രമിക്കൂ."
NO_DATA_MSG = "ഈ വിഷയത്തിൽ ഉചിതമായ വിവരം ഇപ്പോൾ ലഭ്യമല്ല. ദയവായി hospital-ൽ നേരിട്ട് ബന്ധപ്പെടൂ."

GREETING_DEFAULT = "നമസ്കാരം! {hospital_name}-ലേക്ക് സ്വാഗതം. എന്ത് സഹായം ആണ് വേണ്ടത്?"

# ─────────────────────────────────────────────────────────────────────────────
# Malayalam response templates (deterministic, zero-latency)
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATES = {
    # Doctor available
    "doctor_available_yes": "{doctor_name} doctor ഇന്ന് {open_time} മുതൽ {close_time} വരെ available ആണ്.",
    "doctor_available_no": "ക്ഷമിക്കണം, {doctor_name} doctor ഇന്ന് available അല്ല.",
    "doctor_available_unknown": "{doctor_name} doctor-ന്റെ availability ഇപ്പോൾ confirm ചെയ്യാൻ കഴിയുന്നില്ല. ദയവായി hospital-ൽ ബന്ധപ്പെടൂ.",

    # Department doctors
    "dept_doctors_available": "{department} department-ൽ ഇന്ന് {count} doctor(s) available ആണ്: {names}.",
    "dept_doctors_none": "ക്ഷമിക്കണം, {department} department-ൽ ഇന്ന് doctors available അല്ല.",
    "dept_not_found": "ക്ഷമിക്കണം, ഈ hospital-ൽ {query_dept} department ലഭ്യമല്ല.",

    # OP Timing
    "dept_timing_open": "{department} OP {day}-ന് {open_time} മുതൽ {close_time} വരെ ആണ്.",
    "dept_timing_closed": "{department} {day}-ന് OP ഇല്ല.",
    "general_timing": "Hospital {day}-ന് {open_time} മുതൽ {close_time} വരെ open ആണ്.",

    # Fee
    "fee_doctor": "{doctor_name} doctor-ന്റെ consultation fee {currency} {amount} ആണ്.",
    "fee_department": "{department} consultation fee {currency} {amount} ആണ്.",
    "fee_not_found": "Consultation fee-യുടെ കൃത്യമായ വിവരം ഇപ്പോൾ ലഭ്യമല്ല. ദയവായി reception-ൽ ബന്ധപ്പെടൂ.",

    # Department exists
    "dept_exists": "ആം, ഈ hospital-ൽ {department} department ഉണ്ട്{floor_info}.",
    "dept_exists_with_floor": " ({floor}-ൽ ആണ്)",
    "dept_not_exists": "ക്ഷമിക്കണം, ഈ hospital-ൽ {query_dept} department ഇല്ല.",

    # Hospital timing
    "hospital_open": "Hospital ഇന്ന് {open_time} മുതൽ {close_time} വരെ open ആണ്.",
    "hospital_closed": "ക്ഷമിക്കണം, Hospital ഇന്ന് {reason} ആയതിനാൽ അടച്ചിരിക്കുകയാണ്.",
    "hospital_holiday": "ഇന്ന് {reason} ആയതിനാൽ hospital close ആണ്. Emergency-ക്ക് {emergency_phone} ൽ ബന്ധപ്പെടൂ.",
    "hospital_emergency_only": "ഇന്ന് hospital {reason} ആയതിനാൽ Emergency service മാത്രം available ആണ്.",

    # Emergency
    "emergency_yes_24x7": "ആം, ഈ hospital-ൽ 24 മണിക്കൂറും Emergency service ഉണ്ട്. Emergency number: {emergency_phone}.",
    "emergency_yes": "ആം, Emergency service ഉണ്ട്. {notes}",
    "emergency_no": "ക്ഷമിക്കണം, ഈ hospital-ൽ Emergency service ലഭ്യമല്ല. അടുത്തുള്ള hospital-ൽ ബന്ധപ്പെടൂ.",

    # Location
    "location": "Hospital-ന്റെ address: {address}, {city}, {district}.",
    "location_no_address": "Location വിവരം ഇപ്പോൾ ലഭ്യമല്ล. ദയവായി {phone} ൽ ബന്ധപ്പെടൂ.",

    # Contact
    "contact": "Hospital-ന്റെ phone number: {phone_primary}.",
    "contact_multiple": "Reception: {phone_primary}. Emergency: {phone_emergency}.",
}


def _t(template_key: str, **kwargs) -> str:
    """Render a template with variables. Falls back to a safe default."""
    template = _TEMPLATES.get(template_key, "")
    if not template:
        return FALLBACK_MSG
    try:
        return template.format(**kwargs)
    except KeyError:
        return template  # return with unfilled slots rather than crash


# ─────────────────────────────────────────────────────────────────────────────
# Composer
# ─────────────────────────────────────────────────────────────────────────────


class ResponseComposer:
    """
    Converts a KnowledgeResult into a natural Malayalam response string.

    Priority:
      1. Template-based (zero latency)
      2. LLM-based (Claude Haiku, only for edge cases)
    """

    def __init__(self, hospital_name: str = "ഈ Hospital", use_llm: bool = True):
        self.hospital_name = hospital_name
        self.use_llm = use_llm and settings.ENABLE_LLM_RESPONSE and bool(settings.ANTHROPIC_API_KEY)
        self._llm_client: Optional[anthropic.Anthropic] = None
        if self.use_llm:
            self._llm_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def compose(self, result: KnowledgeResult) -> str:
        """
        Compose a short, natural Malayalam response.
        Returns a string ready for TTS.
        """
        handler = {
            INTENT_DOCTOR_AVAILABILITY: self._compose_doctor_availability,
            INTENT_DOCTOR_TIMING: self._compose_doctor_timing,
            INTENT_CONSULTATION_FEE: self._compose_fee,
            INTENT_DEPARTMENT_EXISTS: self._compose_dept_exists,
            INTENT_HOSPITAL_TIMING: self._compose_hospital_timing,
            INTENT_EMERGENCY: self._compose_emergency,
            INTENT_LOCATION: self._compose_location,
            INTENT_CONTACT: self._compose_contact,
        }.get(result.intent)

        if handler:
            return handler(result)

        if result.intent == INTENT_GOODBYE:
            return GOODBYE_MSG
        if result.intent == INTENT_HUMAN_TRANSFER:
            return TRANSFER_MSG
        if result.intent == INTENT_REPEAT:
            return CLARIFICATION_MSGS[0]

        return FALLBACK_MSG

    def clarification(self, attempt: int = 0) -> str:
        idx = min(attempt, len(CLARIFICATION_MSGS) - 1)
        return CLARIFICATION_MSGS[idx]

    def greeting(self) -> str:
        text = _t("greeting", hospital_name=self.hospital_name)
        return GREETING_DEFAULT.format(hospital_name=self.hospital_name)

    def transfer_message(self) -> str:
        return TRANSFER_MSG

    def goodbye(self) -> str:
        return GOODBYE_MSG

    def fallback(self) -> str:
        return FALLBACK_MSG

    # ─── Intent-specific composers ────────────────────────────────────────────

    def _compose_doctor_availability(self, result: KnowledgeResult) -> str:
        d = result.data

        if not result.found:
            if result.missing_entity == "department_or_doctor":
                return "ഏത് doctor-നെ കുറിച്ചാണ് അറിയേണ്ടത്? Department-ന്റെ പേര് പറഞ്ഞാൽ ഞാൻ confirm ചെയ്യാം."
            if result.missing_entity == "doctor_name":
                name = d.get("query_name", "ആ doctor")
                return f"ക്ഷമിക്കണം, {name}-നെ ഞങ്ങളുടെ list-ൽ കണ്ടെത്താൻ കഴിഞ്ഞില്ല. Reception-ൽ ഒരു നിമിഷം hold ചെയ്യൂ."
            if result.missing_entity == "department":
                return f"ക്ഷമിക്കണം, ആ department ഇവിടെ ലഭ്യമല്ല."
            return NO_DATA_MSG

        # Single doctor result
        if "doctor_name" in d:
            if d.get("available") and d.get("slots"):
                slots = d["slots"]
                return _t(
                    "doctor_available_yes",
                    doctor_name=d["doctor_name"],
                    open_time=slots.get("start_time", ""),
                    close_time=slots.get("end_time", ""),
                )
            elif not d.get("available"):
                return _t("doctor_available_no", doctor_name=d["doctor_name"])
            return _t("doctor_available_unknown", doctor_name=d["doctor_name"])

        # Department-level result
        if "available_doctors" in d:
            available = d["available_doctors"]
            dept = d.get("department", "")
            if not available:
                return _t("dept_doctors_none", department=dept)
            names = ", ".join(doc["name"] for doc in available[:3])  # max 3 names in voice
            return _t("dept_doctors_available", department=dept, count=len(available), names=names)

        return FALLBACK_MSG

    def _compose_doctor_timing(self, result: KnowledgeResult) -> str:
        d = result.data
        if not result.found or not d:
            return NO_DATA_MSG

        timing = d.get("timing")
        dept = d.get("department", "")
        doc = d.get("doctor_name", "")
        day = d.get("day", "ഇന്ന്")

        if timing and not timing.get("is_closed", False):
            subject = dept or doc or "OP"
            return _t(
                "dept_timing_open",
                department=subject,
                day=_malayalam_day(day),
                open_time=timing.get("open_time", ""),
                close_time=timing.get("close_time", ""),
            )
        if timing and timing.get("is_closed"):
            return _t("dept_timing_closed", department=dept or doc, day=_malayalam_day(day))

        # General timing fallback
        if d.get("open_time"):
            return _t(
                "general_timing",
                day=_malayalam_day(day),
                open_time=d["open_time"],
                close_time=d.get("close_time", ""),
            )

        return NO_DATA_MSG

    def _compose_fee(self, result: KnowledgeResult) -> str:
        d = result.data
        if not result.found:
            if result.missing_entity == "fee_not_configured":
                return _t("fee_not_found")
            return "Consultation fee-യുടെ വിവരം ലഭ്യമല്ല. Reception-ൽ ബന്ധപ്പെടൂ."

        if "doctor_name" in d:
            return _t(
                "fee_doctor",
                doctor_name=d["doctor_name"],
                currency=d.get("currency", "₹"),
                amount=d.get("amount", ""),
            )
        if "department" in d:
            return _t(
                "fee_department",
                department=d["department"],
                currency=d.get("currency", "₹"),
                amount=d.get("amount", ""),
            )
        return _t("fee_not_found")

    def _compose_dept_exists(self, result: KnowledgeResult) -> str:
        d = result.data
        if result.found:
            floor_info = ""
            if d.get("floor"):
                floor_info = _t("dept_exists_with_floor", floor=d["floor"])
            return _t("dept_exists", department=d.get("department", ""), floor_info=floor_info)
        return _t("dept_not_exists", query_dept=d.get("query_dept", "ആ department"))

    def _compose_hospital_timing(self, result: KnowledgeResult) -> str:
        d = result.data
        if not result.found:
            return NO_DATA_MSG

        if d.get("is_holiday"):
            reason = d.get("reason", "അവധി")
            if d.get("emergency_only"):
                return _t("hospital_emergency_only", reason=reason)
            return _t(
                "hospital_holiday",
                reason=reason,
                emergency_phone=d.get("emergency_phone", "Reception"),
            )

        if d.get("is_open") is False:
            reason = d.get("notes", "")
            return _t("hospital_closed", reason=reason or "ഇന്ന്")

        if d.get("open_time"):
            return _t(
                "hospital_open",
                open_time=d["open_time"],
                close_time=d.get("close_time", ""),
            )

        return NO_DATA_MSG

    def _compose_emergency(self, result: KnowledgeResult) -> str:
        d = result.data
        if not d.get("has_emergency"):
            return _t("emergency_no")
        if d.get("emergency_24x7"):
            return _t("emergency_yes_24x7", emergency_phone=d.get("emergency_phone", "Reception"))
        notes = d.get("notes", "")
        return _t("emergency_yes", notes=notes)

    def _compose_location(self, result: KnowledgeResult) -> str:
        d = result.data
        if not result.found:
            return NO_DATA_MSG
        if not d.get("address"):
            return _t("location_no_address", phone=self.hospital_name)
        return _t(
            "location",
            address=d.get("address", ""),
            city=d.get("city", ""),
            district=d.get("district", ""),
        )

    def _compose_contact(self, result: KnowledgeResult) -> str:
        d = result.data
        if not result.found or not d.get("phone_primary"):
            return NO_DATA_MSG
        if d.get("phone_emergency") and d["phone_emergency"] != d.get("phone_primary"):
            return _t(
                "contact_multiple",
                phone_primary=d["phone_primary"],
                phone_emergency=d["phone_emergency"],
            )
        return _t("contact", phone_primary=d["phone_primary"])


# ─────────────────────────────────────────────────────────────────────────────
# LLM-based response (optional fallback for complex edge cases)
# ─────────────────────────────────────────────────────────────────────────────


def compose_via_llm(
    intent: str,
    data: dict,
    hospital_name: str,
    context: Optional[str] = None,
) -> str:
    """
    Use Claude Haiku to compose a short Malayalam response.
    Only called for edge cases that templates can't cover.
    Target latency: < 300ms (Haiku is fast).

    IMPORTANT: The LLM only phrases the answer. It does NOT generate facts.
    All facts come from `data` dict.
    """
    if not settings.ANTHROPIC_API_KEY:
        return FALLBACK_MSG

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Keep prompt tiny for speed
    prompt = f"""You are the voice system for {hospital_name} hospital in Kerala.
Answer in plain Malayalam (not dialect). Keep it to 1-2 short sentences.
Never invent facts. Only use the data provided.

Intent: {intent}
Data: {data}
{f'Context: {context}' if context else ''}

Reply in Malayalam:"""

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return FALLBACK_MSG


# ─────────────────────────────────────────────────────────────────────────────
# Day name localization
# ─────────────────────────────────────────────────────────────────────────────

_DAY_MALAYALAM = {
    "monday": "തിങ്കൾ",
    "tuesday": "ചൊവ്വ",
    "wednesday": "ബുധൻ",
    "thursday": "വ്യാഴം",
    "friday": "വെള്ളി",
    "saturday": "ശനി",
    "sunday": "ഞായർ",
    "today": "ഇന്ന്",
    "tomorrow": "നാളെ",
    "yesterday": "ഇന്നലെ",
}


def _malayalam_day(day: str) -> str:
    return _DAY_MALAYALAM.get(day.lower(), day)
