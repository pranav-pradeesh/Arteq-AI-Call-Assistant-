"""
Multilingual hospital AI receptionist brain — dual provider.

Full LLM understanding (no keyword matching) with structured JSON output
for routing decisions. Two backends share one conversation history and
system prompt; the provider is chosen per turn by the caller's language:

  English / emergency  → Groq LLaMA (fast, low latency)
      llama-3.1-8b-instant   simple / quick English
      llama-3.3-70b-versatile  emergencies / complex
  Other Indian languages + Manglish → Sarvam-M (built for Indian languages)
      sarvam-m   Malayalam, Hindi, Tamil, Telugu, Kannada, … and code-mixed

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

_MODEL_SMART = "llama-3.3-70b-versatile"
_MODEL_FAST = "llama-3.1-8b-instant"

_SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
_SARVAM_MODEL = "sarvam-m"

# History limit: keep last 20 messages (10 turns)
_MAX_HISTORY = 20

# Limit concurrent API calls across all active calls (free-tier rate limits).
# Groq free tier: 30 RPM per model. Sarvam: per-plan.
_GROQ_SEM = asyncio.Semaphore(5)
_SARVAM_SEM = asyncio.Semaphore(5)


@dataclass
class GroqBrainResult(BrainResult):
    """Extended BrainResult with Groq-specific routing fields."""
    transfer_destination: str = ""   # reception|emergency|opd|billing|pharmacy|lab|patient_relations|doctor
    transfer_doctor: str = ""        # specific doctor name if routing to doctor
    sms_type: str = ""               # maps|appointment|lab_schedule
    sms_data: dict = field(default_factory=dict)
    is_emergency: bool = False
    call_note: str = ""              # brief note for call log


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
        if doc.qualifications:
            lines.append(f"    Qualifications: {doc.qualifications}")
        for slot in doc.slots:
            day_name = _DOW_NAMES[slot.dow] if 0 <= slot.dow <= 6 else str(slot.dow)
            room_part = f" Room {slot.room}" if slot.room else ""
            lines.append(f"    {day_name}: {slot.start}–{slot.end}{room_part}")

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
        for faq in ctx.faqs:
            lines.append(f"  Q: {faq.question}")
            lines.append(f"  A: {faq.answer}")
            if faq.answer_ml:
                lines.append(f"  A (ML): {faq.answer_ml}")

    kb = getattr(ctx, "knowledge_base", "") or ""
    if kb.strip():
        lines.extend([
            "",
            "HOSPITAL HANDBOOK (use this to answer ANY other enquiry — "
            "parking, insurance, facilities, visiting hours, policies, etc.):",
            kb.strip(),
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

    return f"""You are {agent_name}, the AI voice receptionist for {ctx.name}.
Speak naturally and warmly — patients should feel they are talking to a caring human staff member.

TODAY: {today_name}, {current_time} IST

HOSPITAL INFORMATION:
{hospital_summary}

YOUR CAPABILITIES:
1. ROUTING: Connect to Reception, Emergency, OPD, Billing, Pharmacy, Laboratory, Patient Relations, or specific doctors
2. ENQUIRIES: Timings, doctor schedules, fees, services, insurance, parking, visitor policies
3. EMERGENCIES: Detect life-threatening symptoms → immediately route to emergency
4. APPOINTMENTS: Check availability from doctor schedules, offer to connect to reception for booking
5. DIRECTIONS: Hospital address + offer Google Maps link by SMS
6. LAB: Lab timings, test preparation instructions, report availability
7. PHARMACY: Medicine availability, timings, prescription refills
8. PATIENT INFO: Admission status, visiting hours, discharge process
9. BILLING: Bill enquiries, payment, insurance queries

LANGUAGE RULES:
- Detect language from caller's message: Malayalam, English, Hindi, Tamil, Kannada, Telugu, or Manglish
- Manglish = Malayalam words typed/spoken in English script (e.g. "njan doctor-nte time ariyaanam")
- Respond ENTIRELY in the same language/dialect as the caller
- For Malayalam: warm, conversational (not formal)
- For Manglish: respond in natural Manglish
- For English: friendly Indian English

VOICE RULES (you will be converted to speech — make it sound human):
- Max 2 SHORT sentences per response
- Use natural speech patterns: "Sure,", "Of course,", "Let me check..."
- Vary your openings — don't start every response the same way
- For emergencies: speak urgently but calmly

EMERGENCY KEYWORDS (immediate emergency routing regardless of language):
- Chest pain, heart attack, breathless, difficulty breathing
- Stroke, unconscious, fits, seizure
- Heavy bleeding, accident, trauma
- "Emergency", "ambulance", "ICU"
- Malayalam equivalents: നെഞ്ചുവേദന, ശ്വാസതടസ്സം, ബോധക്ഷയം, etc.

ROUTING GUIDE:
- "speak to someone / transfer / human" → reception
- "emergency / ambulance / critical" → emergency
- "OPD / outpatient" → opd
- "bill / payment / insurance" → billing
- "medicine / pharmacy" → pharmacy
- "lab / test / report" → lab
- "Dr. [name]" → doctor (specific)
- "complaint / feedback / patient rights" → patient_relations

SMS TRIGGERS:
- "directions / map / how to reach / location" → offer maps SMS
- "appointment confirmation / schedule details" → offer appointment SMS

ALWAYS respond with valid JSON:
{{
  "text": "what to say to caller (1-2 sentences, natural speech)",
  "language": "ml-IN|en-IN|hi-IN|ta-IN|kn-IN|te-IN|manglish",
  "action": "continue|transfer|end_call|send_sms",
  "transfer_destination": null,
  "transfer_doctor": null,
  "sms_type": null,
  "is_emergency": false,
  "call_note": "5-word note for call log"
}}

transfer_destination must be one of: reception, emergency, opd, billing, pharmacy, lab, patient_relations, doctor — or null.
sms_type must be one of: maps, appointment, lab_schedule — or null."""


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
        """Pick (provider, model) for this turn based on language + urgency."""
        lang = (language_detected or "").lower()
        groq_ok = self._client is not None
        sarvam_ok = bool(self._sarvam_key)
        is_english = lang.startswith("en")

        # Emergencies → Groq smart model for speed and reliability.
        if use_smart and groq_ok:
            return ("groq", _MODEL_SMART)
        # Indian languages / Manglish → Sarvam-M (best multilingual quality).
        if not is_english and sarvam_ok:
            return ("sarvam", _SARVAM_MODEL)
        # English (or Sarvam unavailable) → Groq fast.
        if groq_ok:
            return ("groq", _MODEL_FAST)
        if sarvam_ok:
            return ("sarvam", _SARVAM_MODEL)
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
            "max_tokens": 300,
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
        """Parse the JSON response from Groq into a GroqBrainResult."""
        try:
            clean = raw.strip()
            # Strip Sarvam-M hybrid-reasoning blocks if present
            if "<think>" in clean:
                clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()
            # Strip markdown code fences if present
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            data = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            logger.warning("groq_brain_json_parse_error", raw=raw[:200])
            return GroqBrainResult(
                text="ക്ഷമിക്കണം, ഒരു നിമിഷം — ഞാൻ വീണ്ടും ശ്രമിക്കാം.",
                language=language_detected,
                latency_ms=latency_ms,
            )

        action = data.get("action", "continue")
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
            sms_data={},
            is_emergency=is_emergency,
            call_note=data.get("call_note") or "",
        )

    async def generate_greeting(self) -> GroqBrainResult:
        """
        Return an instant time-aware greeting in Malayalam (no API call).
        Warm, human, with hospital name and agent name.
        """
        now_ist = datetime.now(_INDIA_TZ)
        hour = now_ist.hour

        hosp_name = self._ctx.name_ml or self._ctx.name

        if 5 <= hour < 12:
            # Morning: സുപ്രഭാതം!
            greeting = (
                f"സുപ്രഭാതം! {hosp_name}-ലേക്ക് സ്വാഗതം. "
                f"ഞാൻ {self._agent_name} — എന്ത് സഹായം വേണം?"
            )
        elif 12 <= hour < 17:
            # Afternoon: ശുഭ ഉച്ചനേരം!
            greeting = (
                f"ശുഭ ഉച്ചനേരം! {hosp_name}-ലേക്ക് സ്വാഗതം. "
                f"ഞാൻ {self._agent_name} — എന്ത് സഹായം വേണം?"
            )
        else:
            # Evening: ശുഭ സന്ധ്യ!
            greeting = (
                f"ശുഭ സന്ധ്യ! {hosp_name}-ലേക്ക് സ്വാഗതം. "
                f"ഞാൻ {self._agent_name} — എന്ത് സഹായം വേണം?"
            )

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

        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._history,
        ]

        try:
            if provider == "sarvam":
                raw_text = await self._call_sarvam(messages)
            else:
                raw_text = await self._call_groq(messages, model)

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
                text="ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. Staff-നോട് ബന്ധപ്പെടൂ.",
                language="ml-IN",
                latency_ms=latency_ms,
            )
