"""
Response Composer.

Primary: uses KnowledgeResult.text_ml (pre-built by KnowledgeService).
Fallback: template-based composition from result.data (backward compat).
Edge-case: Groq LLM (only when both above are empty).
"""
from __future__ import annotations

from typing import Optional

from src.config.settings import settings
from src.knowledge.service import KnowledgeResult
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
)

# ── Fixed messages ────────────────────────────────────────────────────────────

CLARIFICATION_MSGS = [
    "ക്ഷമിക്കണം, ശരിയായി കേൾക്കാനായില്ല. ഒന്ന് വീണ്ടും പറയാമോ?",
    "Sorry, clear ആയി കേൾക്കാൻ കഴിഞ്ഞില്ല. വീണ്ടും പറയാമോ?",
    "ഞാൻ ശരിയായി മനസ്സിലാക്കിയില്ല. ഒന്ന് repeat ചെയ്യാമോ?",
]

TRANSFER_MSG  = "ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. ഒരു നിമിഷം."
GOODBYE_MSG   = "നന്ദി. ആരോഗ്യം നിങ്ങൾക്ക് ഉണ്ടാകട്ടെ. ശുഭദിനം."
FALLBACK_MSG  = "ക്ഷമിക്കണം, ഇപ്പോൾ ഉചിതമായ ഒരു ഉത്തരം നൽകാൻ കഴിയുന്നില്ല. Hospital-ൽ നേരിട്ട് ബന്ധപ്പെടൂ."
NO_DATA_MSG   = "ഈ വിഷയത്തിൽ ഉചിതമായ വിവരം ഇപ്പോൾ ലഭ്യമല്ല. ദയവായി hospital-ൽ നേരിട്ട് ബന്ധപ്പെടൂ."

# ── Malayalam templates (used when text_ml is empty) ─────────────────────────

_T = {
    "doctor_available_yes":  "{name} doctor ഇന്ന് {start} മുതൽ {end} വരെ available ആണ്.",
    "doctor_available_no":   "ക്ഷമിക്കണം, {name} doctor ഇന്ന് available അല്ല.",
    "dept_doctors_avail":    "{dept}-ൽ ഇന്ന് {count} doctor available ആണ്: {names}.",
    "dept_doctors_none":     "ക്ഷമിക്കണം, {dept}-ൽ ഇന്ന് doctors available അല്ല.",
    "fee_dept":              "{dept} consultation fee ₹{amount} ({currency}) ആണ്.",
    "fee_doctor":            "{name} doctor-ന്റെ consultation fee ₹{amount} ({currency}) ആണ്.",
    "dept_exists":           "ആം, ഈ hospital-ൽ {dept} department ഉണ്ട്{floor}.",
    "dept_not_exists":       "ക്ഷമിക്കണം, ഈ hospital-ൽ {dept} department ഇല്ല.",
    "timing_open":           "{subject} {day}-ന് {start} മുതൽ {end} വരെ ആണ്.",
    "timing_closed":         "{subject} {day}-ന് available അല്ല.",
    "hospital_open":         "Hospital ഇന്ന് {start} മുതൽ {end} വരെ open ആണ്.",
    "emergency_24x7":        "ആം, 24 മണിക്കൂറും Emergency service ഉണ്ട്. Emergency: {phone}.",
    "emergency_yes":         "ആം, Emergency service ഉണ്ട്.",
    "emergency_no":          "ക്ഷമിക്കണം, ഈ hospital-ൽ Emergency service ലഭ്യമല്ല.",
    "location":              "Hospital address: {address}.",
    "contact":               "Hospital phone: {phone}.",
}


def _t(key: str, **kw) -> str:
    tpl = _T.get(key, "")
    if not tpl:
        return FALLBACK_MSG
    try:
        return tpl.format(**kw)
    except KeyError:
        return tpl


# ── Main composer ─────────────────────────────────────────────────────────────

class ResponseComposer:
    """
    Converts a KnowledgeResult into ready-to-speak Malayalam text.

    Priority:
      1. result.text_ml  (pre-built by KnowledgeService — zero latency)
      2. Template from result.data  (backward-compat, still zero latency)
      3. Groq LLM (edge cases only)
    """

    def __init__(self, hospital_name: str = "ഈ Hospital", use_llm: bool = True):
        self.hospital_name = hospital_name
        # use_llm flag: respected but Groq only activates when API key is set
        self._use_llm = use_llm and bool(settings.GROQ_API_KEY)

    def compose(self, result: KnowledgeResult) -> str:
        # 1. Pre-built text from knowledge service
        if result.text_ml:
            return result.text_ml

        # 2. Special intents
        if result.intent == INTENT_GOODBYE:
            return GOODBYE_MSG
        if result.intent == INTENT_HUMAN_TRANSFER:
            return TRANSFER_MSG
        if result.intent == INTENT_REPEAT:
            return CLARIFICATION_MSGS[0]

        # 3. Template-based fallback
        handler = {
            INTENT_DOCTOR_AVAILABILITY: self._compose_doctor_avail,
            INTENT_DOCTOR_TIMING:       self._compose_timing,
            INTENT_CONSULTATION_FEE:    self._compose_fee,
            INTENT_DEPARTMENT_EXISTS:   self._compose_dept_exists,
            INTENT_HOSPITAL_TIMING:     self._compose_hospital_timing,
            INTENT_EMERGENCY:           self._compose_emergency,
            INTENT_LOCATION:            self._compose_location,
            INTENT_CONTACT:             self._compose_contact,
        }.get(result.intent)
        if handler:
            text = handler(result)
            if text:
                return text

        # 4. Groq LLM for truly unknown edge cases
        if self._use_llm and result.data:
            text = _compose_via_groq(result.intent, result.data, self.hospital_name)
            if text:
                return text

        return FALLBACK_MSG

    def clarification(self, attempt: int = 0) -> str:
        return CLARIFICATION_MSGS[min(attempt, len(CLARIFICATION_MSGS) - 1)]

    def greeting(self) -> str:
        return f"നമസ്കാരം! {self.hospital_name}-ലേക്ക് സ്വാഗതം. എന്ത് സഹായം ആണ് വേണ്ടത്?"

    def transfer_message(self) -> str:
        return TRANSFER_MSG

    def goodbye(self) -> str:
        return GOODBYE_MSG

    def fallback(self) -> str:
        return FALLBACK_MSG

    # ── Template handlers ─────────────────────────────────────────────────────

    def _compose_doctor_avail(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found:
            name = d.get("query_name") or d.get("doctor_name", "ആ doctor")
            return f"ക്ഷമിക്കണം, {name}-നെ ഞങ്ങളുടെ list-ൽ കണ്ടെത്താൻ കഴിഞ്ഞില്ല."
        if "doctor_name" in d:
            name = d["doctor_name"]
            if d.get("available") and d.get("slots"):
                s = d["slots"]
                start = s.get("start_time", s.get("start", ""))
                end   = s.get("end_time",   s.get("end", ""))
                return _t("doctor_available_yes", name=name, start=start, end=end)
            return _t("doctor_available_no", name=name)
        if "available_doctors" in d:
            avail = d["available_doctors"]
            dept  = d.get("department", "")
            if not avail:
                return _t("dept_doctors_none", dept=dept)
            names = ", ".join(doc.get("name", "") for doc in avail[:3])
            return _t("dept_doctors_avail", dept=dept, count=len(avail), names=names)
        return FALLBACK_MSG

    def _compose_timing(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found or not d:
            return NO_DATA_MSG
        subject = d.get("department") or d.get("doctor_name", "OP")
        day     = d.get("day", "ഇന്ന്")
        timing  = d.get("timing", {})
        if timing and not timing.get("is_closed"):
            return _t("timing_open", subject=subject, day=day,
                      start=timing.get("open_time", ""), end=timing.get("close_time", ""))
        return _t("timing_closed", subject=subject, day=day)

    def _compose_fee(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found:
            return "Consultation fee-യുടെ വിവരം ലഭ്യമല്ല. Reception-ൽ ബന്ധപ്പെടൂ."
        amt = d.get("amount", "")
        cur = d.get("currency", "INR")
        if "doctor_name" in d:
            return _t("fee_doctor", name=d["doctor_name"], amount=amt, currency=cur)
        if "department" in d:
            return _t("fee_dept", dept=d["department"], amount=amt, currency=cur)
        return "Consultation fee-യുടെ വിവരം ലഭ്യമല്ല. Reception-ൽ ബന്ധപ്പെടൂ."

    def _compose_dept_exists(self, r: KnowledgeResult) -> str:
        d = r.data
        if r.found:
            floor = f" ({d['floor']}-ൽ ആണ്)" if d.get("floor") else ""
            return _t("dept_exists", dept=d.get("department", ""), floor=floor)
        return _t("dept_not_exists", dept=d.get("query_dept", "ആ department"))

    def _compose_hospital_timing(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found:
            return NO_DATA_MSG
        if d.get("open_time"):
            return _t("hospital_open", start=d["open_time"], end=d.get("close_time", ""))
        return NO_DATA_MSG

    def _compose_emergency(self, r: KnowledgeResult) -> str:
        d = r.data
        if not d.get("has_emergency"):
            return _t("emergency_no")
        if d.get("emergency_24x7"):
            return _t("emergency_24x7", phone=d.get("emergency_phone", "Reception"))
        return _t("emergency_yes")

    def _compose_location(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found or not d.get("address"):
            return NO_DATA_MSG
        return _t("location", address=d["address"])

    def _compose_contact(self, r: KnowledgeResult) -> str:
        d = r.data
        if not r.found or not d.get("phone_primary"):
            return NO_DATA_MSG
        return _t("contact", phone=d["phone_primary"])


# ── Groq LLM fallback ─────────────────────────────────────────────────────────

def _compose_via_groq(intent: str, data: dict, hospital_name: str) -> Optional[str]:
    try:
        from groq import Groq
        client = Groq(api_key=settings.GROQ_API_KEY)
        prompt = (
            f"You are the voice assistant for {hospital_name} hospital in Kerala.\n"
            f"Reply in plain Malayalam (2 sentences max). Never invent facts.\n"
            f"Intent: {intent}\nData: {data}\nReply in Malayalam:"
        )
        resp = client.chat.completions.create(
            model=settings.GROQ_MODEL_FAST,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=settings.GROQ_MAX_TOKENS,
            timeout=settings.GROQ_TIMEOUT_S,
        )
        text = resp.choices[0].message.content.strip()
        return text if text else None
    except Exception:
        return None
