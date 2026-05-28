"""
Gemini 2.5 Flash brain — multilingual hospital AI receptionist.

Replaces keyword engine + Groq with a single function-calling LLM pipeline.
Language auto-detection: responds in the caller's detected language.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytz

from src.config.settings import settings
from src.db.queries import HospitalContext
from src.observability.logger import get_logger

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")
_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


@dataclass
class BrainResult:
    text: str              # response text in caller's language
    language: str          # BCP-47 code of the response language
    should_transfer: bool = False
    should_end: bool = False
    latency_ms: int = 0


def _build_hospital_summary(ctx: HospitalContext) -> str:
    """Build a rich text summary of the hospital for Gemini's system prompt."""
    lines = [
        f"HOSPITAL: {ctx.name} | {ctx.name_ml}",
        f"ADDRESS: {ctx.address}",
        f"PHONE: {ctx.phone}",
        "",
        "OPERATING HOURS:",
    ]
    day_map = {"mon":"Monday","tue":"Tuesday","wed":"Wednesday","thu":"Thursday",
               "fri":"Friday","sat":"Saturday","sun":"Sunday"}
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
        for faq in ctx.faqs[:10]:  # Limit to first 10 for prompt size
            lines.append(f"  Q: {faq.question}")
            lines.append(f"  A: {faq.answer}")
            if faq.answer_ml:
                lines.append(f"  A (ML): {faq.answer_ml}")

    return "\n".join(lines)


def _build_system_prompt(ctx: HospitalContext, agent_name: str) -> str:
    hospital_data = _build_hospital_summary(ctx)
    now_ist = datetime.now(_INDIA_TZ)
    # Python weekday(): 0=Mon..6=Sun. Convert to Sun=0..Sat=6
    py_dow = now_ist.weekday()
    today_dow = (py_dow + 1) % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    today_name = _DOW_NAMES[today_dow]
    current_time = now_ist.strftime("%H:%M")

    return f"""You are {agent_name}, an AI voice receptionist for a hospital. You answer calls from patients and their families.

TODAY: {today_name}, {current_time} IST

CRITICAL RULES:
1. LANGUAGE: Detect the caller's language from their message and respond ENTIRELY in that language. If they speak Malayalam, respond in Malayalam. If English, respond in English. If Hindi, respond in Hindi. If they mix languages (Manglish), respond in Malayalam with English medical terms.
2. BREVITY: Voice responses must be under 2 sentences. This will be converted to audio. No lists, no bullet points.
3. ACCURACY: Only state information from the hospital data below. Never invent doctor names, timings, or fees.
4. EMERGENCY: If caller mentions chest pain, difficulty breathing, severe bleeding, stroke symptoms, or any life-threatening emergency, immediately give emergency number and say to call ambulance.
5. ROLE: You are a receptionist. You answer questions about departments, doctors, timings, fees, directions. You do NOT give medical advice or diagnose.
6. TRANSFER: Only request transfer to human when caller insists or when the query is beyond your scope.

{hospital_data}

MALAYALAM DEPARTMENT NAMES (for matching callers who say dept name in Malayalam):
Use the dept name_ml field above. Common: ഗൈനക്കോളജി=Gynaecology, കാർഡിയോളജി=Cardiology, ന്യൂറോളജി=Neurology, ഓർത്തോപീഡിക്സ്=Orthopedics, ശിശുരോഗം=Pediatrics, ത്വക്ക്=Dermatology, ENT=ENT"""


# ── Function declarations for Gemini ─────────────────────────────────────────

_FUNCTION_DECLARATIONS = [
    {
        "name": "request_human_transfer",
        "description": "Request transfer to a human staff member. Call this ONLY when: (1) caller explicitly asks to speak to a human/receptionist/doctor, OR (2) the query is a complex complaint/emergency that needs human intervention, OR (3) caller is frustrated after 2+ unclear exchanges.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for transfer"
                }
            },
            "required": []
        }
    },
    {
        "name": "end_call_gracefully",
        "description": "End the call when the caller says goodbye, thank you and seems done, or explicitly says they're done.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


class GeminiBrain:
    """
    Gemini 2.5 Flash function-calling brain.
    One instance per call — maintains multi-turn conversation history.
    """

    def __init__(self, hospital_context: HospitalContext, agent_name: str = "Arya"):
        self._ctx = hospital_context
        self._agent_name = agent_name
        self._history: list = []
        self._system_prompt = _build_system_prompt(hospital_context, agent_name)
        self._client = None  # lazy init

        # Import here to avoid import errors if not installed
        try:
            from google import genai
            from google.genai import types as genai_types
            self._genai = genai
            self._types = genai_types
            if settings.GEMINI_API_KEY:
                self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except ImportError:
            logger.warning("google_genai_not_installed")

    def is_available(self) -> bool:
        return self._client is not None

    async def process(
        self,
        transcript: str,
        language_detected: str = "ml-IN",
    ) -> BrainResult:
        if not self._client:
            return BrainResult(
                text="ക്ഷമിക്കണം, AI service ലഭ്യമല്ല.",
                language="ml-IN",
            )

        t_start = time.monotonic()
        try:
            types = self._types

            # Build tools
            fn_decls = [
                types.FunctionDeclaration(**fd) for fd in _FUNCTION_DECLARATIONS
            ]
            tools = [types.Tool(function_declarations=fn_decls)]

            # Build contents: history + current user message
            contents = [
                *self._history,
                types.Content(
                    role="user",
                    parts=[types.Part(text=transcript)]
                ),
            ]

            response = await self._client.aio.models.generate_content(
                model="gemini-2.5-flash-preview-05-20",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self._system_prompt,
                    tools=tools,
                    temperature=0.3,
                    max_output_tokens=300,
                ),
            )

            latency_ms = int((time.monotonic() - t_start) * 1000)

            # Check for function calls
            should_transfer = False
            should_end = False
            response_text = ""

            candidate = response.candidates[0] if response.candidates else None
            if candidate:
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        fn_name = part.function_call.name
                        if fn_name == "request_human_transfer":
                            should_transfer = True
                        elif fn_name == "end_call_gracefully":
                            should_end = True
                    if hasattr(part, 'text') and part.text:
                        response_text += part.text

            if not response_text and should_transfer:
                response_text = "ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു."
            elif not response_text and should_end:
                response_text = "നന്ദി, ആരോഗ്യം ആശംസിക്കുന്നു. Goodbye!"
            elif not response_text:
                response_text = "ക്ഷമിക്കണം, ഒരു നിമിഷം."

            # Update conversation history (keep last 10 turns to avoid context overflow)
            self._history.append(
                types.Content(role="user", parts=[types.Part(text=transcript)])
            )
            self._history.append(candidate.content if candidate else
                                  types.Content(role="model", parts=[types.Part(text=response_text)]))
            # Trim history to last 20 messages (10 turns)
            if len(self._history) > 20:
                self._history = self._history[-20:]

            logger.info("gemini_brain_ok",
                        latency_ms=latency_ms,
                        language=language_detected,
                        transfer=should_transfer,
                        end=should_end,
                        response_preview=response_text[:80])

            return BrainResult(
                text=response_text,
                language=language_detected,
                should_transfer=should_transfer,
                should_end=should_end,
                latency_ms=latency_ms,
            )

        except Exception as e:
            logger.error("gemini_brain_error", error=str(e), transcript=transcript[:60])
            return BrainResult(
                text="ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. Staff-നോട് ബന്ധപ്പെടൂ.",
                language="ml-IN",
            )

    async def generate_greeting(self) -> BrainResult:
        """Generate a natural greeting in the hospital's primary language."""
        now_ist = datetime.now(_INDIA_TZ)
        hour = now_ist.hour
        if 5 <= hour < 12:
            time_greet = "Good morning / സുപ്രഭാതം"
        elif 12 <= hour < 17:
            time_greet = "Good afternoon / ശുഭ ഉച്ചനേരം"
        elif 17 <= hour < 21:
            time_greet = "Good evening / ശുഭ സന്ധ്യ"
        else:
            time_greet = "Good evening / ശുഭ സന്ധ്യ"

        hosp_name = self._ctx.name_ml or self._ctx.name
        greeting = (
            f"{time_greet}! Welcome to {hosp_name}. "
            f"ഞാൻ {self._agent_name} ആണ്, ഇവിടത്തെ AI assistant. "
            f"Doctor timing, fees, departments — എന്ത് സഹായം വേണം?"
        )
        return BrainResult(text=greeting, language=settings.DEFAULT_LANGUAGE)
