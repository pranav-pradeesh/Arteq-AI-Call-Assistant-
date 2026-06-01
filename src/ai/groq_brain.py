"""
Multilingual hospital AI receptionist brain — dual provider.

Full LLM understanding (no keyword matching) with structured JSON output
for routing decisions. Two backends share one conversation history and
system prompt; the provider is chosen per turn:

  All languages (default)  → Sarvam-M (built for Indian languages, incl. English)
      sarvam-m   Malayalam, Hindi, Tamil, Telugu, Kannada, Manglish, English …
  Emergencies              → Groq llama-3.3-70b-versatile (fast, high quality)
      auto-falls-back to Sarvam-M if Groq is rate-limited

Sarvam-M is primary because Groq's free tier (6000 TPM) cannot sustain a
multi-turn voice call and the Dev-tier upgrade is currently unavailable.
Both endpoints are OpenAI-compatible (same {role, content} message list),
so history is portable across providers within a single call.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
import pytz

from src.ai.base import BrainResult
from src.config.settings import settings
from src.db.queries import HospitalContext
from src.observability.logger import get_logger

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")
_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# Human-readable language names used to steer the reply language each turn.
_LANG_NAMES = {
    "ml-IN": "Malayalam",
    "en-IN": "English",
    "ta-IN": "Tamil",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "te-IN": "Telugu",
    "manglish": "Manglish (Malayalam written in English/Latin script)",
}


def build_greeting_text(hosp_name: str, agent_name: str, hour: int) -> str:
    """Time-of-day Malayalam greeting. Pure function so it can be pre-warmed."""
    if 5 <= hour < 12:
        opener = "സുപ്രഭാതം!"        # morning
    elif 12 <= hour < 17:
        opener = "ശുഭ ഉച്ചനേരം!"      # afternoon
    else:
        opener = "ശുഭ സന്ധ്യ!"        # evening
    return (
        f"{opener} {hosp_name}-ലേക്ക് സ്വാഗതം. "
        f"ഞാൻ {agent_name}. എങ്ങനെ സഹായിക്കാം?"
    )

_MODEL_SMART = "llama-3.3-70b-versatile"
_MODEL_FAST = "llama-3.1-8b-instant"

_SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
_SARVAM_MODEL = "sarvam-m"

# History limit: keep last 8 messages (4 turns). Kept small because the
# system prompt is large and Groq's free tier caps at 6000 tokens/minute —
# every turn re-sends the full prompt, so trimming history protects the budget.
_MAX_HISTORY = 8

# Hard cap on the hospital "handbook" free-text injected into the prompt.
# The full KB (insurance + lab + policies) can be several thousand tokens;
# capping keeps each request well under the Groq free-tier TPM limit.
_MAX_KB_CHARS = 1200

# Limit concurrent API calls across all active calls (free-tier rate limits).
# Groq free tier: 30 RPM per model. Sarvam: per-plan.
_GROQ_SEM = asyncio.Semaphore(5)
_SARVAM_SEM = asyncio.Semaphore(5)


@dataclass
class GroqBrainResult(BrainResult):
    """Extended BrainResult with Groq-specific routing fields."""
    transfer_destination: str = ""   # reception|emergency|opd|billing|pharmacy|lab|patient_relations|doctor
    transfer_doctor: str = ""        # specific doctor name if routing to doctor
    sms_type: str = ""               # maps|appointment|appointment_cancel|callback_confirm|lab_schedule|call_summary
    sms_data: dict = field(default_factory=dict)
    is_emergency: bool = False
    call_note: str = ""              # brief note for call log
    # Extended action types for IVR features
    action_type: str = ""            # book_appointment|cancel_appointment|reschedule_appointment|request_callback|repeat_last
    appointment_data: dict = field(default_factory=dict)  # {patient_name,doctor_name,dept,date,time,notes}
    callback_data: dict = field(default_factory=dict)     # {reason,preferred_time}
    repeat_requested: bool = False


def _is_groq_exhausted(exc: Exception) -> bool:
    """True if a Groq error means we should fall back to Sarvam-M.

    Covers rate limits (429), token-per-minute caps (413 'request too large'),
    and transient server/availability errors.
    """
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "rate_limit", "rate limit", "429",
            "413", "too large", "payload too large",
            "tokens per minute", "tpm",
            "503", "502", "500", "overloaded", "service unavailable",
        )
    )


def _build_hospital_summary(ctx: HospitalContext) -> str:
    """Build a rich text summary of the hospital for the system prompt."""
    lines = [
        f"HOSPITAL: {ctx.name} | {ctx.name_ml}",
        f"ADDRESS: {ctx.address}",
        f"PHONE: {ctx.phone}",
        "",
        "OPERATING HOURS:",
    ]
    day_map = {
        "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
        "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    }
    for abbr, full in day_map.items():
        slot = ctx.hours.get(abbr)
        if slot:
            lines.append(f"  {full}: {slot[0]} - {slot[1]}")
        else:
            lines.append(f"  {full}: Closed")

    lines.extend(["", "DEPARTMENTS:"])
    for d in ctx.departments:
        ml_part = f" ({d.name_ml})" if d.name_ml else ""
        floor_part = f" — Floor {d.floor}" if d.floor else ""
        ext_part = f" — Ext {d.phone_ext}" if d.phone_ext else ""
        lines.append(f"  • {d.name}{ml_part}{floor_part}{ext_part}")

    lines.extend(["", "DOCTORS & SCHEDULES:"])
    for doc in ctx.doctors:
        ml_name = f" ({doc.name_ml})" if doc.name_ml else ""
        lines.append(f"  Dr. {doc.name}{ml_name} — {doc.dept_name}")
        # Condense schedule into one line per doctor (e.g. "Mon 9:00–13:00; Wed 10:00–12:00")
        if doc.slots:
            parts = []
            for slot in doc.slots:
                day_name = _DOW_NAMES[slot.dow] if 0 <= slot.dow <= 6 else str(slot.dow)
                parts.append(f"{day_name[:3]} {slot.start}–{slot.end}")
            lines.append(f"    {'; '.join(parts)}")

    lines.extend(["", "CONSULTATION FEES:"])
    for b in ctx.billing:
        dept_key = b.item.replace("consultation:", "")
        ml_part = f" ({b.item_ml})" if b.item_ml else ""
        price = f"₹{int(b.price_min)}–₹{int(b.price_max)}"
        notes = f" ({b.notes})" if b.notes else ""
        lines.append(f"  {dept_key}{ml_part}: {price}{notes}")

    lines.extend(["", "EMERGENCY CONTACTS:"])
    for e in ctx.emergency:
        ml_part = f" ({e.label_ml})" if e.label_ml else ""
        lines.append(f"  {e.label}{ml_part}: {e.phone}")

    if ctx.faqs:
        lines.extend(["", "FAQ:"])
        # English answers only — the model translates as needed.
        for faq in ctx.faqs:
            lines.append(f"  Q: {faq.question}")
            lines.append(f"  A: {faq.answer}")

    if ctx.queue_data:
        lines.extend(["", "OPD QUEUE TODAY (approximate):"])
        for dept_name, count in ctx.queue_data.items():
            lines.append(f"  {dept_name}: ~{count} patients in queue")

    kb = (getattr(ctx, "knowledge_base", "") or "").strip()
    if kb:
        if len(kb) > _MAX_KB_CHARS:
            kb = kb[:_MAX_KB_CHARS].rsplit(" ", 1)[0] + " …"
        lines.extend([
            "",
            "HOSPITAL HANDBOOK (use this to answer ANY other enquiry — "
            "parking, insurance, facilities, visiting hours, policies, etc.):",
            kb,
        ])

    return "\n".join(lines)


def _build_system_prompt(ctx: HospitalContext, agent_name: str) -> str:
    """Construct the full system prompt for the Groq LLaMA model."""
    hospital_summary = _build_hospital_summary(ctx)
    now_ist = datetime.now(_INDIA_TZ)
    py_dow = now_ist.weekday()
    today_dow = (py_dow + 1) % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    today_name = _DOW_NAMES[today_dow]
    current_time = now_ist.strftime("%H:%M")

    return f"""You are {agent_name}, the warm AI voice receptionist for {ctx.name}. Patients should feel they're talking to a caring human.

TODAY: {today_name}, {current_time} IST

HOSPITAL INFORMATION:
{hospital_summary}

WHAT YOU DO: route calls (reception, emergency, opd, billing, pharmacy, lab, patient_relations, or a specific doctor); answer enquiries (timings, schedules, fees, services, insurance, parking, visiting hours) using the info above; detect emergencies and route immediately; help with appointments, directions, lab/pharmacy/billing questions.

APPOINTMENT BOOKING (multi-turn conversation):
When a caller wants to book an appointment, collect these details across turns:
  1. Patient name (ask if not given)
  2. Preferred doctor or department
  3. Preferred date and time
  Once all details are collected, respond with action_type="book_appointment" and fill appointment_data.
  appointment_data format: {{"patient_name":"...","doctor_name":"...","dept":"...","date":"YYYY-MM-DD","time":"HH:MM","notes":"..."}}
  Always offer to SMS the confirmation (set sms_type="appointment").

APPOINTMENT CANCELLATION / RESCHEDULE:
  If caller says cancel/reschedule appointment → use action_type="cancel_appointment" or "reschedule_appointment".
  For cancel: appointment_data={{"patient_name":"...","doctor_name":"...","date":"..."}}
  For reschedule: appointment_data={{"patient_name":"...","new_date":"YYYY-MM-DD","new_time":"HH:MM"}}
  Offer SMS confirmation (sms_type="appointment_cancel" or "appointment").

CALLBACK REQUEST:
  If caller says "call me back", "oru call back venam", "oru call back cheyyaamo", "later call cheyyanam" →
  Confirm their request, ask reason and preferred time.
  Use action_type="request_callback", callback_data={{"reason":"...","preferred_time":"..."}}
  Offer SMS confirmation (sms_type="callback_confirm").

AFTER-HOURS:
  Check TODAY's time against OPERATING HOURS above. If the hospital is currently CLOSED and the caller needs OPD/doctor:
  - Tell them the next opening time.
  - Offer: (a) book for tomorrow / next opening (action_type="book_appointment"), or
           (b) callback when open (action_type="request_callback"), or
           (c) if urgent — transfer to emergency immediately.
  Never say "we are closed, goodbye." Always offer an option.

OPD QUEUE / WAIT TIME:
  If OPD QUEUE TODAY data is shown above, use it to give an estimate.
  Without data, say "token number depends on arrival time — come early for less wait."

REPEAT LAST RESPONSE:
  If caller says "pardon", "sorry?", "what?", "oru kuri koodi", "oru kuri koodi parayaamo", "kettu", "again", "again parayo" →
  Use action_type="repeat_last". Do NOT generate new content.

DTMF DIGIT FALLBACK:
  If caller says a digit or number as their entire message ("1", "2", "ഒന്ന്", "two", etc.) →
  Treat it as selecting from this menu: 1=OPD, 2=Emergency/Casualty, 3=Lab/Laboratory, 4=Pharmacy, 5=Billing, 0=Reception, *=repeat.
  Respond as if they asked about that department.

POST-CALL SMS:
  After completing any transaction (booking / cancellation / callback registered), if the caller is ending,
  set sms_type="call_summary" and include a brief summary in sms_data={{"summary":"..."}}.

LAB REPORTS: Direct to the lab counter or give the WhatsApp/pickup info from the handbook.
BILL INQUIRY: Give estimated cost from CONSULTATION FEES; offer to transfer to billing for exact amount.
VISITING HOURS / INSURANCE / BLOOD BANK / PARKING: Answer from HOSPITAL HANDBOOK.
DIRECTIONS: Send maps SMS (sms_type="maps").

LANGUAGE (CRITICAL): Always reply in the SAME language and script as the caller's most recent message — Malayalam, English, Hindi, Tamil, Kannada, Telugu, or Manglish (Malayalam in English script, e.g. "njan doctor-nte time ariyaanam"). Never switch to English unless the caller spoke English. If the caller speaks Malayalam, reply in Malayalam script; if Manglish, reply in Manglish. Malayalam/Manglish should be warm and conversational, not formal.

VOICE (your text becomes speech): max 2 SHORT sentences. Sound human and vary your openings. English openers: "Sure,", "Of course,", "Let me check…". Malayalam openers: "ശരി,", "തീർച്ചയായും,", "ഒന്ന് നോക്കട്ടെ,", "ങ്ഹാ,". For emergencies, speak urgently but calmly.

MALAYALAM STYLE (sound like a real Kerala hospital receptionist on the phone, NOT a news reader):
- Use everyday SPOKEN Malayalam (സംസാരഭാഷ), warm and simple — never stiff, literary, or Sanskritised. Say "എന്താ വേണ്ടേ?" not "എന്ത് ആവശ്യമാണ്?".
- Keep common medical/English terms in English the way Keralites actually speak — doctor, appointment, OPD, token, casualty, lab, scan, report, booking, consultation, emergency, timing. Do NOT translate these into rare words (say "OPD timing", never "ബാഹ്യരോഗവിഭാഗ സമയം").
- Be polite and warm: "ദയവായി", "പറയൂ", "സഹായിക്കാം", optional "സാർ"/"മാഡം". Avoid the stiff "താങ്കൾ"; a pronoun is often unnecessary.
- Use natural connectors/fillers sparingly: "ശരി", "അതെ", "പിന്നെ", "ഉം".
- Times and numbers: write naturally for speech, e.g. "രാവിലെ 9 മണി മുതൽ ഉച്ചയ്ക്ക് 1 മണി വരെ", "₹500". Use രാവിലെ / ഉച്ചയ്ക്ക് / വൈകുന്നേരം / രാത്രി instead of AM/PM.
- For Manglish callers, reply in Manglish (Latin script): "Doctor-inte OPD timing രാവിലെ 9 muthal aanu" style — mix exactly the way the caller does.

EMERGENCY (route to emergency, is_emergency=true): chest pain, heart attack, breathless, stroke, unconscious, seizure/fits, heavy bleeding, accident, "ambulance"/"ICU", or Malayalam equivalents (നെഞ്ചുവേദന, ശ്വാസതടസ്സം, ബോധക്ഷയം).

SMS: offer maps SMS for directions/location; offer appointment SMS for confirmations.

ALWAYS respond with valid JSON only — no extra text, no markdown:
{{"text":"1-2 natural sentences","language":"ml-IN|en-IN|hi-IN|ta-IN|kn-IN|te-IN|manglish","action":"continue|transfer|end_call|send_sms","action_type":"","transfer_destination":null,"transfer_doctor":null,"sms_type":null,"sms_data":{{}},"appointment_data":{{}},"callback_data":{{}},"is_emergency":false,"call_note":"5-word log note"}}

action_type values:
  "book_appointment"       — appointment_data has all booking fields; will be written to DB
  "cancel_appointment"     — appointment_data identifies which appointment to cancel
  "reschedule_appointment" — appointment_data has new_date/new_time
  "request_callback"       — callback_data has reason+preferred_time; will be written to DB
  "repeat_last"            — replay previous response (do not generate new text)
  ""                       — normal turn (no side-effect)

action values:
  "continue"   — keep call going (default)
  "transfer"   — route to transfer_destination
  "end_call"   — hang up after speaking text
  "send_sms"   — send SMS (also set sms_type)

transfer_destination ∈ {{reception, emergency, opd, billing, pharmacy, lab, patient_relations, doctor}} or null.
sms_type ∈ {{maps, appointment, appointment_cancel, callback_confirm, lab_schedule, call_summary}} or null."""


class GroqBrain:
    """
    Groq LLaMA brain for the hospital voice assistant.
    One instance per call — maintains multi-turn conversation history.
    """

    def __init__(self, hospital_context: HospitalContext, agent_name: str = "Arya") -> None:
        self._ctx = hospital_context
        self._agent_name = agent_name
        self._history: list[dict] = []
        self._system_prompt = _build_system_prompt(hospital_context, agent_name)

        try:
            from groq import AsyncGroq  # type: ignore[import-untyped]
            groq_api_key: str = getattr(settings, "GROQ_API_KEY", "")
            self._client: Optional[AsyncGroq] = AsyncGroq(api_key=groq_api_key) if groq_api_key else None
        except ImportError:
            logger.warning("groq_not_installed")
            self._client = None

        # Sarvam-M chat — raw httpx (OpenAI-compatible), reuses the Sarvam key.
        self._sarvam_key: str = getattr(settings, "SARVAM_API_KEY", "")

    def is_available(self) -> bool:
        return self._client is not None or bool(self._sarvam_key)

    def _route(self, language_detected: str, use_smart: bool) -> tuple[str, str]:
        """Pick (provider, model) for this turn based on language + urgency.

        Sarvam-M is primary for ALL languages (including English) because
        Groq's free tier (6000 TPM) cannot sustain a multi-turn voice call and
        the Dev-tier upgrade is currently unavailable. Groq's smart model is
        used only for emergencies (and auto-falls-back to Sarvam-M if Groq is
        rate-limited). If Groq Dev tier becomes available later, English can be
        routed back to Groq fast for lower latency.
        """
        groq_ok = self._client is not None
        sarvam_ok = bool(self._sarvam_key)

        # Emergencies → Groq smart model for speed/quality (falls back to
        # Sarvam-M automatically in process() if Groq is rate-limited).
        if use_smart and groq_ok:
            return ("groq", _MODEL_SMART)
        # Default for every language: Sarvam-M (no free-tier TPM bottleneck).
        if sarvam_ok:
            return ("sarvam", _SARVAM_MODEL)
        # Last resort if Sarvam is not configured.
        if groq_ok:
            return ("groq", _MODEL_FAST)
        return ("none", "")

    async def _call_groq(self, messages: list[dict], model: str) -> str:
        """Call Groq with concurrency cap + retry on rate limit."""
        if not self._client:
            raise RuntimeError("groq_unavailable")
        response = None
        for attempt in range(3):
            try:
                async with _GROQ_SEM:
                    response = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.3,
                        max_tokens=300,
                        response_format={"type": "json_object"},
                    )
                break
            except Exception as exc:
                msg = str(exc).lower()
                if ("rate_limit" in msg or "rate limit" in msg or "429" in msg) and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                raise
        return response.choices[0].message.content or ""

    async def _call_sarvam(self, messages: list[dict]) -> str:
        """Call Sarvam-M (OpenAI-compatible) with concurrency cap + retry."""
        if not self._sarvam_key:
            raise RuntimeError("sarvam_unavailable")
        payload = {
            "model": _SARVAM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            # sarvam-m is a hybrid-reasoning model. With a small budget its
            # <think> phase eats every token and no JSON is emitted. Disable
            # thinking (documented: reasoning_effort=None) AND keep a generous
            # budget so that even if a think block slips through it can close
            # and still leave room for the JSON answer.
            "reasoning_effort": None,
            "max_tokens": 800,
        }
        headers = {
            "api-subscription-key": self._sarvam_key,
            "Content-Type": "application/json",
        }
        async with _SARVAM_SEM:
            for attempt in range(3):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(12.0, connect=3.0)
                ) as client:
                    resp = await client.post(_SARVAM_CHAT_URL, headers=headers, json=payload)
                if resp.status_code in (429, 503) and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"sarvam_chat HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
        return ""

    def _parse_response(self, raw: str, language_detected: str, latency_ms: int) -> GroqBrainResult:
        """Parse the JSON response (Groq or Sarvam-M) into a GroqBrainResult.

        Robust to Sarvam-M reasoning artefacts: strips <think> blocks (closed
        OR unclosed) and markdown fences, then extracts the JSON object by its
        outermost braces so surrounding prose can't break parsing.
        """
        # Strip any reasoning block first — closed (<think>…</think>) or not.
        clean = raw.strip()
        if "</think>" in clean:
            clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()
        elif "<think>" in clean:
            clean = clean.split("<think>", 1)[-1].strip()

        data = None
        try:
            candidate = clean
            # Isolate the JSON object by its outermost braces (drops any prose,
            # markdown fences, or leftover reasoning text around it).
            start, end = candidate.find("{"), candidate.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = candidate[start : end + 1]
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            data = None

        if not isinstance(data, dict):
            # The model sometimes answers in plain prose instead of JSON. That
            # prose is a perfectly good spoken reply — speak it rather than a
            # canned apology. Only fall back to the apology if there's no usable
            # text or it looks like broken JSON.
            looks_like_json = clean.lstrip().startswith("{")
            if clean and not looks_like_json:
                logger.info("brain_prose_fallback", text=clean[:120])
                return GroqBrainResult(
                    text=clean,
                    language=language_detected,
                    latency_ms=latency_ms,
                )
            logger.warning("groq_brain_json_parse_error", raw=raw[:200])
            return GroqBrainResult(
                text="ക്ഷമിക്കണം, ഒരു നിമിഷം — ഞാൻ വീണ്ടും ശ്രമിക്കാം.",
                language=language_detected,
                latency_ms=latency_ms,
            )

        action = data.get("action", "continue")
        action_type = data.get("action_type") or ""
        transfer_dest = data.get("transfer_destination") or ""
        is_emergency = bool(data.get("is_emergency", False))

        return GroqBrainResult(
            text=data.get("text", ""),
            language=data.get("language", language_detected),
            should_transfer=(action == "transfer"),
            should_end=(action == "end_call"),
            latency_ms=latency_ms,
            transfer_destination=transfer_dest,
            transfer_doctor=data.get("transfer_doctor") or "",
            sms_type=data.get("sms_type") or "",
            sms_data=data.get("sms_data") or {},
            is_emergency=is_emergency,
            call_note=data.get("call_note") or "",
            action_type=action_type,
            appointment_data=data.get("appointment_data") or {},
            callback_data=data.get("callback_data") or {},
            repeat_requested=(action_type == "repeat_last"),
        )

    async def generate_greeting(self) -> GroqBrainResult:
        """
        Return an instant time-aware greeting in Malayalam (no API call).
        Warm, human, with hospital name and agent name.
        """
        now_ist = datetime.now(_INDIA_TZ)
        hosp_name = self._ctx.name_ml or self._ctx.name
        greeting = build_greeting_text(hosp_name, self._agent_name, now_ist.hour)
        return GroqBrainResult(
            text=greeting,
            language=settings.DEFAULT_LANGUAGE,
        )

    async def process(
        self,
        transcript: str,
        language_detected: str = "ml-IN",
    ) -> GroqBrainResult:
        """
        Process a caller transcript and return a structured response.

        Routes per turn: English/emergency → Groq LLaMA; other Indian
        languages and Manglish → Sarvam-M. History is shared across both.
        """
        if not self._client and not self._sarvam_key:
            return GroqBrainResult(
                text="ക്ഷമിക്കണം, AI service ലഭ്യമല്ല. ദയവായി staff-നോട് ബന്ധപ്പെടൂ.",
                language="ml-IN",
            )

        t_start = time.monotonic()

        # Append user turn
        self._history.append({"role": "user", "content": transcript})

        # Emergency keywords force the fast, reliable Groq smart model.
        _emergency_hints = (
            "emergency", "ambulance", "chest pain", "unconscious", "breathing",
            "bleeding", "accident", "stroke", "seizure", "fits",
            "നെഞ്ചുവേദന", "ശ്വാസ", "ബോധക്ഷയം",
        )
        use_smart = any(kw in transcript.lower() for kw in _emergency_hints)
        provider, model = self._route(language_detected, use_smart)

        # Per-turn language steer: append to the system prompt (Sarvam-M only
        # allows ONE system message — a second "role":"system" gets HTTP 400).
        lang_name = _LANG_NAMES.get(language_detected, "the caller's language")
        per_turn_system = (
            self._system_prompt
            + f"\n\nCURRENT TURN: The caller just spoke in {lang_name}. "
            f"Reply ONLY in {lang_name}, matching their script and dialect. "
            f"Do not switch languages."
        )

        messages = [
            {"role": "system", "content": per_turn_system},
            *self._history,
        ]

        try:
            if provider == "sarvam":
                raw_text = await self._call_sarvam(messages)
            else:
                try:
                    raw_text = await self._call_groq(messages, model)
                except Exception as groq_exc:
                    # Groq exhausted (rate limit / 413 token cap / outage):
                    # fall back to Sarvam-M so the call doesn't die. This makes
                    # the system resilient even on Groq's free tier.
                    if self._sarvam_key and _is_groq_exhausted(groq_exc):
                        logger.warning(
                            "groq_fallback_to_sarvam", error=str(groq_exc)[:200]
                        )
                        raw_text = await self._call_sarvam(messages)
                        provider = "sarvam"
                        model = _SARVAM_MODEL
                    else:
                        raise

            latency_ms = int((time.monotonic() - t_start) * 1000)
            result = self._parse_response(raw_text, language_detected, latency_ms)

            # Append model turn (store the parsed text, not raw, so a switched
            # provider on the next turn sees clean conversational history).
            self._history.append({"role": "assistant", "content": result.text})

            # Trim history to last _MAX_HISTORY messages
            if len(self._history) > _MAX_HISTORY:
                self._history = self._history[-_MAX_HISTORY:]

            logger.info(
                "brain_ok",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                language=result.language,
                action="transfer" if result.should_transfer else ("end" if result.should_end else "continue"),
                dest=result.transfer_destination,
                emergency=result.is_emergency,
                response_preview=result.text[:80],
            )

            return result

        except Exception as exc:
            latency_ms = int((time.monotonic() - t_start) * 1000)
            logger.error("brain_error", provider=provider, error=str(exc),
                         transcript=transcript[:60])
            # Remove the user message we optimistically appended
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            return GroqBrainResult(
                text="ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. ദയവായി ഒന്നൂടെ പറയാമോ?",
                language="ml-IN",
                latency_ms=latency_ms,
            )
