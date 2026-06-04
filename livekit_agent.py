"""
Arteq Hospital Voice Agent — LiveKit 1.5.x edition.

Full-featured AI receptionist for Kerala hospitals:
  • Silero VAD → Sarvam STT (Saaras v3, 23 languages, codemix)
  • Groq LLaMA 70B (via OpenAI-compatible base_url)
  • Sarvam TTS (Bulbul v3, Malayalam, "shubh" voice)
  • Acoustic Sensory Layer — detects patient distress from PCM stats
  • Function tools — book/cancel appointments, callbacks, SMS, emergency
  • Multi-tenant — room name = "{slug}-call-{uuid}", context from DB

Run:
  python livekit_agent.py dev      # development (auto-join)
  python livekit_agent.py start    # production worker pool

Required env vars:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
  SARVAM_API_KEY, GROQ_API_KEY
  DATABASE_URL
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("livekit.agents")

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, APIConnectOptions
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents import llm as agents_llm  # ChatContext, ChatMessage types
from livekit.plugins import openai, sarvam, silero
from openai import AsyncClient as _AsyncOpenAI  # raw SDK, for custom-header Sarvam client

load_dotenv()

# ── Sarvam Bulbul v3 TTS ────────────────────────────────────────────────────────
# livekit-plugins-sarvam 1.1.7 only knows bulbul:v2 and unconditionally sends
# `pitch` and `loudness`. Bulbul v3 rejects those two params (400). Subclass the
# stream to drop them while keeping v3's better Malayalam voices.

import base64 as _b64
import aiohttp as _aiohttp
from livekit.agents import (
    APIConnectionError as _APIConnErr,
    APIStatusError as _APIStatusErr,
    APITimeoutError as _APITimeoutErr,
)
from livekit.plugins.sarvam.tts import (
    TTS as _SarvamTTS,
    ChunkedStream as _SarvamChunkedStream,
    MODEL_SPEAKER_COMPATIBILITY as _SARVAM_COMPAT,
    logger as _sarvam_log,
)


# Unicode script ranges → Sarvam Bulbul v3 target_language_code. Bulbul speaks
# the text in the phonetics of this language, so it MUST match the script the
# LLM replied in — otherwise an English/Hindi/Tamil reply gets Malayalam
# phonetics. Detected per-utterance so one agent handles every caller language.
_SCRIPT_RANGES = [
    ("ml-IN", 0x0D00, 0x0D7F),  # Malayalam
    ("ta-IN", 0x0B80, 0x0BFF),  # Tamil
    ("te-IN", 0x0C00, 0x0C7F),  # Telugu
    ("kn-IN", 0x0C80, 0x0CFF),  # Kannada
    ("hi-IN", 0x0900, 0x097F),  # Devanagari (Hindi/Marathi)
    ("bn-IN", 0x0980, 0x09FF),  # Bengali
    ("gu-IN", 0x0A80, 0x0AFF),  # Gujarati
    ("pa-IN", 0x0A00, 0x0A7F),  # Gurmukhi (Punjabi)
    ("od-IN", 0x0B00, 0x0B7F),  # Odia
]


def _detect_tts_lang(text: str, fallback: str) -> str:
    """Pick target_language_code from the dominant Indic script in `text`.

    Counts characters per script; the script with the most chars wins. Pure
    Latin (no Indic chars) → en-IN. Empty/unknown → the configured fallback.
    """
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        cp = ord(ch)
        if 0x41 <= cp <= 0x7A and ch.isalpha():
            latin += 1
            continue
        for code, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if counts:
        return max(counts, key=counts.get)
    if latin:
        return "en-IN"
    return fallback


def _tts_cache_key(opts, text: str, lang: str) -> str:
    import hashlib
    raw = f"{opts.model}|{lang}|{opts.speaker}|{opts.pace}|{opts.speech_sample_rate}|{text}"
    return "tts:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


class _BulbulV3ChunkedStream(_SarvamChunkedStream):
    async def _run(self, output_emitter) -> None:
        # Audio cache: identical text+voice → identical audio. The greeting is
        # synthesized on every call, so caching it (O(1) lookup) removes a full
        # Bulbul round-trip from the first thing the caller hears. LRU + 24h TTL
        # keep the hottest phrases (greeting, confirmations) warm; dynamic
        # replies churn out harmlessly at the cold end.
        from src.cache.store import tts_cache, TTS_CACHE_TTL
        # Detect reply language per-utterance so one agent speaks every caller's
        # language (the LLM replies in their language; we match the voice to it).
        lang = _detect_tts_lang(self._input_text, self._opts.target_language_code)
        cache_key = _tts_cache_key(self._opts, self._input_text, lang)
        cached = tts_cache.get(cache_key)
        if cached is not None:
            output_emitter.initialize(
                request_id="cache",
                sample_rate=self._tts.sample_rate,
                num_channels=self._tts.num_channels,
                mime_type="audio/wav",
            )
            for chunk in cached:
                output_emitter.push(chunk)
            return

        # v3-only payload: pitch, loudness and enable_preprocessing are all
        # rejected by bulbul:v3, so none are sent. pace + speech_sample_rate are
        # the only voice knobs v3 accepts here.
        payload = {
            "target_language_code": lang,
            "text": self._input_text,
            "speaker": self._opts.speaker,
            "pace": self._opts.pace,
            "speech_sample_rate": self._opts.speech_sample_rate,
            "model": self._opts.model,
        }
        headers = {
            "api-subscription-key": self._opts.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with self._tts._ensure_session().post(
                url=self._opts.base_url,
                json=payload,
                headers=headers,
                timeout=_aiohttp.ClientTimeout(
                    total=self._conn_options.timeout,
                    sock_connect=self._conn_options.timeout,
                ),
            ) as res:
                if res.status != 200:
                    error_text = await res.text()
                    _sarvam_log.error(f"Sarvam TTS API error: {res.status} - {error_text}")
                    raise _APIStatusErr(
                        message=f"Sarvam TTS API Error: {error_text}", status_code=res.status
                    )
                response_json = await res.json()
                request_id = response_json.get("request_id", "")
                audios = response_json.get("audios", [])
                if not audios or not isinstance(audios, list):
                    raise _APIConnErr("Sarvam TTS API response invalid: no audio data")
                output_emitter.initialize(
                    request_id=request_id or "unknown",
                    sample_rate=self._tts.sample_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/wav",
                )
                decoded = [_b64.b64decode(b64) for b64 in audios]
                for chunk in decoded:
                    output_emitter.push(chunk)
                tts_cache.set(cache_key, decoded, ttl=TTS_CACHE_TTL)
        except asyncio.TimeoutError as e:
            raise _APITimeoutErr("Sarvam TTS API request timed out") from e
        except _aiohttp.ClientError as e:
            raise _APIConnErr(f"Sarvam TTS API connection error: {e}") from e


# Bulbul v3 voice roster (Sarvam docs). v3 rejects pitch, loudness and
# enable_preprocessing; its native sample rate is 24000 Hz (v2 used 22050).
_BULBUL_V3_SPEAKERS = [
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
]

# Teach the upstream (v2-only) plugin about v3 so its built-in speaker check
# validates against the real v3 roster instead of logging "unknown model" and
# skipping validation on every construction.
_SARVAM_COMPAT["bulbul:v3"] = {
    "all": list(_BULBUL_V3_SPEAKERS),
    "female": [],
    "male": [],
}


class BulbulV3TTS(_SarvamTTS):
    """Sarvam Bulbul v3 TTS.

    The upstream plugin models only bulbul:v2 — it defaults to a v2 speaker at
    22050 Hz and its ChunkedStream always sends pitch/loudness. This subclass
    targets v3 purely:
      - forces model="bulbul:v3" and the v3-native 24000 Hz sample rate,
      - validates the speaker against the v3 roster (via the parent, now that v3
        is registered) — a bad voice fails fast instead of a raw 400,
      - emits a payload with ONLY v3-accepted fields (no pitch / loudness /
        enable_preprocessing) through _BulbulV3ChunkedStream.
    """

    def __init__(self, *, speaker: str = "priya", speech_sample_rate: int = 24000, **kwargs):
        kwargs.pop("model", None)   # v3 is enforced; ignore any caller override
        super().__init__(
            model="bulbul:v3",
            speaker=speaker,
            speech_sample_rate=speech_sample_rate,
            **kwargs,
        )

    def synthesize(self, text: str, *, conn_options=None):
        from livekit.agents import DEFAULT_API_CONNECT_OPTIONS
        if conn_options is None:
            conn_options = DEFAULT_API_CONNECT_OPTIONS
        return _BulbulV3ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_CTX = 4   # keep system prompt + last 4 messages (2 turns) — Groq TPM is tight

_DTMF = {
    "1": "OPD timing please",
    "2": "emergency help needed",
    "3": "lab test timings",
    "4": "pharmacy location and timing",
    "5": "billing inquiry",
    "0": "transfer to reception desk",
    "*": "please repeat that",
    "#": "thank you goodbye",
}


def _build_llm(premium: bool = True):
    """Resilient LLM with a 3-leg fallback chain: 70b → 8b → Sarvam.

    `premium` (tenant feature flag) picks the Groq primary: 70b when on, 8b when
    off. The chain matters because Groq's free tier enforces a *per-model* daily
    token cap (TPD, 100k): when 70b's cap is exhausted it 429s every turn. Each
    model has its OWN bucket, so llama-3.1-8b-instant keeps serving — and it's
    sub-second, vs Sarvam's ~12s. So 8b is the fast middle leg; Sarvam (Indian-
    language, fully separate provider/quota) is the last resort that guarantees
    Arya never goes silent even if all of Groq is down.

    Sarvam's OpenAI-compatible endpoint authenticates with an
    `api-subscription-key` header, not Bearer, so it needs a custom client.
    """
    def _groq(model: str) -> openai.LLM:
        return openai.LLM(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY", ""),
            # Malayalam script is token-dense, so 200 truncates a 2-sentence
            # reply mid-word; 512 fits a full reply. Low temp curbs llama-3.3's
            # habit of emitting its <function=...> tool syntax as spoken text.
            model=model,
            max_completion_tokens=512,
            temperature=0.4,
        )

    chain = [_groq("llama-3.3-70b-versatile"), _groq("llama-3.1-8b-instant")] \
        if premium else [_groq("llama-3.1-8b-instant")]

    sarvam_key = os.getenv("SARVAM_API_KEY", "")
    if sarvam_key:
        chain.append(openai.LLM(
            # sarvam-m was deprecated by Sarvam (returns 400). sarvam-30b is the
            # current Indian-language chat model — separate provider, so it keeps
            # answering when all Groq legs are capped. ~12s latency, hence last.
            model="sarvam-30b",
            temperature=0.4,
            client=_AsyncOpenAI(
                api_key=sarvam_key,
                base_url="https://api.sarvam.ai/v1",
                default_headers={"api-subscription-key": sarvam_key},
            ),
        ))

    if len(chain) == 1:
        return chain[0]
    return agents_llm.FallbackAdapter(chain)


# ==============================================================================
# Acoustic Sensory Layer
# ==============================================================================

class AcousticSensoryLayer:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._rms: list[float] = []
        self._zcr: list[float] = []

    def feed(self, frame: rtc.AudioFrame) -> None:
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float64)
        if pcm.size == 0:
            return
        self._rms.append(float(np.sqrt(np.mean(pcm ** 2))))
        self._zcr.append(float(np.count_nonzero(np.diff(pcm > 0))))

    def metadata(self) -> str:
        if not self._rms:
            return ""
        avg_vol = float(np.mean(self._rms))
        avg_zcr = float(np.mean(self._zcr))
        vol_var = float(np.var(self._rms))
        zcr_var = float(np.var(self._zcr))
        vol = "HIGH" if avg_vol > 1500 else ("LOW" if avg_vol < 300 else "NORMAL")
        pit = "HIGH" if avg_zcr > 80 else ("LOW" if avg_zcr < 30 else "NORMAL")
        stb = "TREMBLING" if (zcr_var > 400 or vol_var > 50_000) else "STEADY"
        if vol == "NORMAL" and pit == "NORMAL" and stb == "STEADY":
            return ""
        return f"[SENSORY: VOL={vol}, PITCH={pit}, TENSION={stb}]"


# ==============================================================================
# Hospital context helpers
# ==============================================================================

async def _resolve_call_target(room_name: str) -> tuple[str, dict, Optional[dict]]:
    """Resolve room -> (hospital_id, features, tenant).

    Looks the slug up in the control-DB tenant registry. If that tenant has its
    OWN database (db_url), binds this call's async context to that DB so every
    subsequent query routes there. The hospital row is then resolved inside the
    correct database. Falls back to single-DB / settings.HOSPITAL_ID on miss.
    """
    slug = room_name.split("-call-")[0] if "-call-" in room_name else room_name
    from src.config.settings import settings
    features: dict = {}
    tenant: Optional[dict] = None

    try:
        from src.tenancy import registry
        from src.db.queries import set_tenant_db_url
        tenant = await registry.get_tenant(slug.lower())
        if tenant:
            features = tenant.get("features", {}) or {}
            if tenant.get("db_url"):
                set_tenant_db_url(tenant["db_url"])
    except Exception as exc:
        print(f"[warn] tenant registry lookup failed: {exc}", file=sys.stderr)

    try:
        from src.db.queries import get_pool
        pool = await get_pool()   # tenant pool if bound above, else control
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM hospitals WHERE "
                "slug=$1 OR LOWER(REPLACE(name,' ','-'))=$1 LIMIT 1",
                slug.lower(),
            )
        hospital_id = str(row["id"]) if row else settings.HOSPITAL_ID
    except Exception as exc:
        print(f"[warn] hospital ID lookup failed: {exc}", file=sys.stderr)
        hospital_id = settings.HOSPITAL_ID

    return hospital_id, features, tenant


async def _load_hospital_ctx(hospital_id: str):
    try:
        from src.db.queries import get_or_load_hospital_context
        return await get_or_load_hospital_context(hospital_id)
    except Exception as exc:
        print(f"[warn] hospital context load failed: {exc}", file=sys.stderr)
        return None


async def _load_patient_profile(caller_phone: str, hospital_id: str) -> Optional[dict]:
    try:
        from src.db.queries import get_appointments_by_phone
        appts = await get_appointments_by_phone(caller_phone, hospital_id)
        if not appts:
            return None
        return {
            "name": appts[0].get("patient_name", ""),
            "history": [
                {
                    "doctor": a.get("doctor_name", ""),
                    "slot": str(a["slot_time"])[:16] if a.get("slot_time") else "",
                    "status": a.get("status", ""),
                }
                for a in appts[:3]
            ],
        }
    except Exception:
        return None


# ==============================================================================
# System prompt builder
# ==============================================================================

def _build_prompt(hospital_ctx, agent_name: str, outbound_context: Optional[dict]) -> str:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(_IST)
    _DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    day_name = _DAYS[(now.weekday() + 1) % 7]
    time_str = now.strftime("%H:%M")

    if hospital_ctx:
        try:
            from src.ai.groq_brain import _build_hospital_summary
            hosp_block = _build_hospital_summary(hospital_ctx)
        except Exception:
            hosp_block = f"Hospital: {hospital_ctx.name}"
        hosp_name = hospital_ctx.name_ml or hospital_ctx.name
        dow = (now.weekday() + 1) % 7
        hours = hospital_ctx.hours_for_day(dow)
        if hours:
            open_t, close_t = hours
            open_status = (
                f"OPEN {open_t}–{close_t}"
                if open_t <= time_str <= close_t
                else f"CLOSED (opens {open_t})"
            )
        else:
            open_status = "Hours not listed"
    else:
        hosp_block = "Hospital information not available."
        hosp_name = "the hospital"
        open_status = "Unknown"

    outbound_block = ""
    if outbound_context:
        call_type = outbound_context.get("call_type", "")
        pname = outbound_context.get("patient_name", "")
        dname = outbound_context.get("doctor_name", "")
        date  = outbound_context.get("appointment_date", "")
        ttime = outbound_context.get("appointment_time", "")
        if call_type == "confirmation":
            outbound_block = (
                f"\nOUTBOUND CONFIRMATION CALL:\n"
                f"You are calling to confirm {pname}'s appointment with Dr. {dname} "
                f"on {date} at {ttime}.\n"
                "First sentence: state the appointment and ask if they can attend.\n"
                "If YES → use book_appointment to confirm. If NO → offer to reschedule.\n"
            )
        elif call_type == "reminder":
            outbound_block = (
                f"\nOUTBOUND REMINDER CALL:\n"
                f"Reminding {pname} of appointment with Dr. {dname} on {date} at {ttime}.\n"
                "Keep it brief — just the reminder and ask if there are any questions.\n"
            )
        elif call_type == "callback":
            outbound_block = (
                f"\nOUTBOUND CALLBACK:\n"
                f"Calling {pname} back as requested. Ask how you can help today.\n"
            )
        elif call_type == "followup":
            outbound_block = (
                f"\nOUTBOUND FOLLOW-UP:\n"
                f"Calling {pname} 3 days after their appointment with Dr. {dname}.\n"
                "Ask how they are feeling and if they need anything.\n"
            )

    return f"""You are {agent_name}, the warm AI voice receptionist for {hosp_name}.

STYLE: Reply in the caller's language (Malayalam default; also Hindi/Tamil/Kannada/Telugu/English/Manglish). Max 2 SHORT sentences, end with ONE question. Warm, human, not robotic.

SCRIPT: Keep EVERY English and medical word in plain English letters — Yes, No, OK, ICU, OPD, scanning, copy, SMS, appointment, cardiology, doctor. NEVER transliterate an English word into Malayalam script: say "Yes"/"No"/"OK", never "ഇയെസ്"/"ഇയേസ്"/"നോ"/"ഓകെ"; never "കാപ്പി", never "ഫങ്ഷൻ". Malayalam words stay in Malayalam script.

ANSWER INSTANTLY from the HOSPITAL section below — NO tool, NO "let me check" — for: whether a department exists, its floor/location, operating hours, open/closed, doctor names and their department, emergency numbers, address, phone, and anything in the HANDBOOK. You already know these; just say the answer.

USE A TOOL ONLY for live data or write actions, and call it SILENTLY: check_availability (is a doctor free), book_appointment (collect name+doctor+date+time), reschedule_appointment, cancel_appointment, get_doctor_schedule (exact timings), request_callback, send_location_sms, transfer_to_department, alert_emergency. Before booking, repeat name, doctor, date and time back to confirm.

NEVER invent doctor names, timings, fees, or availability — if it is neither in the HOSPITAL section nor a tool result, transfer.

CRITICAL: Your spoken reply is plain natural language ONLY. NEVER write code, JSON, or function/tool syntax (no "<function=...>", no "{...}"). NEVER announce or narrate tool use — do NOT say "I am calling a function", "let me check", "fetching details", "collecting information", "gathering information" or anything similar. Speak ONLY the final answer.

If a [SENSORY:...] tag shows TENSION=TREMBLING or VOL/PITCH=LOW → patient may be in pain/frightened: speak very gently, reassure first.

EMERGENCY (chest pain, severe bleeding, unconscious, can't breathe, stroke, poisoning): call alert_emergency FIRST, say "Connecting you to emergency — please stay on the line."

DIGITS: 1=OPD/doctor 2=emergency 3=lab 4=pharmacy 5=billing 0=reception *=repeat

AFTER HOURS: if CLOSED, give next opening and offer (a) book for then, (b) callback, or (c) emergency. Never say "closed, goodbye".
{outbound_block}
HOSPITAL:
{hosp_block}

TODAY: {day_name}, {time_str} IST | STATUS: {open_status}"""


def _build_greeting(hospital_ctx, agent_name: str, outbound_context: Optional[dict],
                    returning_name: str = "") -> str:
    hosp_name = (hospital_ctx.name_ml or hospital_ctx.name) if hospital_ctx else "the hospital"

    if outbound_context:
        call_type = outbound_context.get("call_type", "")
        pname = outbound_context.get("patient_name", "")
        dname = outbound_context.get("doctor_name", "")
        date  = outbound_context.get("appointment_date", "")
        ttime = outbound_context.get("appointment_time", "")
        if call_type == "confirmation":
            return (
                f"Namaste {pname}, ഞാൻ {agent_name} — {hosp_name}-ൽ നിന്നും. "
                f"Dr. {dname}-ന്റെ appointment {date} {ttime}-ന് confirm ചെയ്യാൻ "
                f"വിളിക്കുകയാണ്. ഈ appointment attend ചെയ്യാൻ കഴിയുമോ?"
            )
        elif call_type == "reminder":
            return (
                f"Namaste {pname}, {agent_name} speaking from {hosp_name}. "
                f"Dr. {dname}-ന്റെ appointment {date}-ന് ഉണ്ടെന്ന് ഓർമ്മിപ്പിക്കാൻ വിളിച്ചതാണ്. "
                "എന്തെങ്കിലും ചോദ്യങ്ങൾ ഉണ്ടോ?"
            )
        elif call_type == "callback":
            return f"Namaste {pname}, {agent_name} speaking — {hosp_name}. How can I help you today?"
        elif call_type == "followup":
            return (
                f"Namaste {pname}, {agent_name} here from {hosp_name}. "
                f"Dr. {dname}-ൽ നിന്നുള്ള visit കഴിഞ്ഞ് എങ്ങനെ feel ചെയ്യുന്നു?"
            )

    try:
        import pytz
        hour = datetime.now(pytz.timezone("Asia/Kolkata")).hour
        from src.ai.groq_brain import build_greeting_text
        from src.config.settings import settings
        base = build_greeting_text(hosp_name, settings.AGENT_NAME, hour)
    except Exception:
        base = f"Namaste! {hosp_name}-ലേക്ക് സ്വാഗതം. ഞാൻ {agent_name}. എങ്ങനെ സഹായിക്കാം?"

    # Returning caller recognised by phone → greet by name in the very first line
    # (something a menu-tree IVR can never do).
    if returning_name:
        first = returning_name.split()[0]
        return f"Namaste {first}! {base}"
    return base


# ==============================================================================
# Agent class — one per call session
# ==============================================================================

class HospitalVoiceAgent(Agent):
    """Arteq hospital voice agent — wraps the full pipeline per call."""

    def __init__(
        self,
        system_prompt: str,
        greeting: str,
        tools: list,
        sensory: AcousticSensoryLayer,
        hospital_id: str,
        caller_phone: str,
        call_id: str,
        hospital_name: str,
        call_started_at: datetime,
        agent_language: str = "ml-IN",
        premium_llm: bool = True,
        vad=None,
    ) -> None:
        super().__init__(
            instructions=system_prompt,
            tools=tools,
            stt=sarvam.STT(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="saaras:v3",
                language="unknown",
            ),
            # Reuse the worker-prewarmed VAD (loaded once in prewarm_fnc) so the
            # Silero model load is off the per-call critical path. Fall back to a
            # fresh load if prewarm was skipped. 0.2s end-of-speech silence →
            # Arya starts replying sooner; still long enough not to cut a caller
            # in a natural mid-sentence pause.
            vad=vad or silero.VAD.load(min_silence_duration=0.2),
            llm=_build_llm(premium=premium_llm),
            tts=BulbulV3TTS(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                # Bulbul requires the target language code; without it the
                # request 400s and no audio is produced. Model (bulbul:v3),
                # speaker and 24000 Hz sample rate are enforced by BulbulV3TTS.
                target_language_code=agent_language,
                speaker="priya",
            ),
        )
        self._greeting = greeting
        self._sensory = sensory
        self._hospital_id = hospital_id
        self._caller_phone = caller_phone
        self._call_id = call_id
        self._hospital_name = hospital_name
        self._call_started_at = call_started_at

    async def on_enter(self) -> None:
        """Speak the opening greeting when the call connects.

        Uses session.say() so the greeting is sent straight to TTS without an
        LLM round-trip — guarantees the exact Malayalam phrase plays back, and
        is the pattern Sarvam docs recommend for fixed openings.
        """
        await self.session.say(self._greeting, allow_interruptions=True)

    async def on_user_turn_completed(
        self,
        turn_ctx: agents_llm.ChatContext,
        new_message: agents_llm.ChatMessage,
    ) -> None:
        """
        Intercept each user turn for:
          1. DTMF digit → synthetic phrase
          2. Acoustic metadata injection
          3. Context window pruning
        """
        # Extract plain text from content (ChatContent = str | ImageContent | AudioContent)
        text = ""
        try:
            content = new_message.content
            if isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, str):
                        text += chunk
            elif isinstance(content, str):
                text = content
        except Exception:
            text = ""

        stripped = text.strip()

        # DTMF: single digit → remap to natural language phrase
        if stripped in _DTMF:
            try:
                new_message.content = [_DTMF[stripped]]
            except Exception:
                pass
            self._sensory.reset()
            return

        # Inject acoustic metadata when noteworthy
        meta = self._sensory.metadata()
        self._sensory.reset()
        if meta and text:
            try:
                new_message.content = [f"{meta}\n{text}"]
            except Exception:
                pass

        # Context pruning. truncate() keeps the last N items and re-inserts the
        # system prompt at the front. ChatContext.messages is a METHOD in
        # agents 1.5.x (not a list), and there is no _messages attr — the old
        # turn_ctx.messages / turn_ctx._messages code raised and silently
        # skipped, so context grew unbounded.
        try:
            turn_ctx.truncate(max_items=_MAX_CTX + 1)
        except Exception:
            pass

    async def tts_node(self, text, model_settings):
        """Streaming tool-syntax stripper — lowest latency for voice.

        Groq's llama-3.3-70b sometimes emits a tool call as literal text
        (`<function=name>{json}</function>` or a `<tool_call>` block) instead of
        through the API tool channel; spoken aloud it is gibberish.

        Rather than buffer the whole reply (which would delay first audio until
        the LLM finished), we strip incrementally: flush every chunk of clean
        text the instant it is provably outside a tool tag, and only hold back
        the minimal tail that could still be the start of one. TTS therefore
        starts on the first words while the LLM is still generating the rest.
        """
        async def _clean():
            buf = ""
            async for chunk in text:
                buf += chunk
                buf = _TOOL_SYNTAX_COMPLETE_RE.sub("", buf)   # drop closed blocks
                emit, buf = _split_safe(buf)                   # hold only tag-prefix tail
                if emit:
                    yield emit
            tail = _strip_tool_syntax(buf)                     # flush any unterminated tail
            if tail:
                yield tail

        async for frame in Agent.default.tts_node(self, _clean(), model_settings):
            yield frame


# Complete tool-call blocks (have a closing tag) — safe to remove mid-stream.
_TOOL_SYNTAX_COMPLETE_RE = re.compile(
    r"<function\s*=.*?</function\s*>|<tool_call>.*?</tool_call>",
    re.DOTALL | re.IGNORECASE,
)

# Any tool-call markup including an unterminated tail — used at stream end.
_TOOL_SYNTAX_RE = re.compile(
    r"<function\s*=.*?</function\s*>"
    r"|<tool_call>.*?</tool_call>"
    r"|<function\s*=.*$"
    r"|<tool_call>.*$",
    re.DOTALL | re.IGNORECASE,
)

_TOOL_OPENERS = ("<function", "<tool_call")


def _split_safe(buf: str) -> tuple[str, str]:
    """Split into (emit_now, hold). Hold from the first '<' that could begin a
    tool opener — everything before it is provably speakable."""
    for i, ch in enumerate(buf):
        if ch == "<":
            tail = buf[i:].lower()
            if any(op.startswith(tail) or tail.startswith(op) for op in _TOOL_OPENERS):
                return buf[:i], buf[i:]
    return buf, ""


def _strip_tool_syntax(text: str) -> str:
    return _TOOL_SYNTAX_RE.sub("", text).strip()


# ==============================================================================
# Agent entrypoint
# ==============================================================================

async def entrypoint(ctx: JobContext) -> None:
    """One LiveKit room = one call. Called by the WorkerOptions dispatcher."""
    await ctx.connect()
    room_name = ctx.room.name
    call_id = str(uuid.uuid4())
    call_started_at = datetime.now(timezone.utc)
    _log.info("arteq call room=%s call_id=%s", room_name, call_id[:8])

    # ── Hospital context (tenant-aware: binds to the tenant's own DB) ──────────
    hospital_id, tenant_features, _tenant = await _resolve_call_target(room_name)
    hospital_ctx  = await _load_hospital_ctx(hospital_id)
    hospital_name = hospital_ctx.name if hospital_ctx else "Arteq Hospital"
    hospital_tier = getattr(hospital_ctx, "tier", "hospital") if hospital_ctx else "hospital"

    # ── Outbound context from room metadata ───────────────────────────────────
    outbound_context: Optional[dict] = None
    try:
        if ctx.room.metadata:
            import json as _json
            data = _json.loads(ctx.room.metadata)
            if data.get("call_type"):
                outbound_context = data
    except Exception:
        pass

    # ── Caller phone from participant identity ────────────────────────────────
    caller_phone = ""
    patient_profile: Optional[dict] = None
    try:
        for p in ctx.room.remote_participants.values():
            ident = p.identity or p.name or ""
            if ident.startswith("+") or (ident.startswith("91") and len(ident) >= 12):
                caller_phone = ident if ident.startswith("+") else f"+{ident}"
                break
        from src.tenancy.features import enabled as _feat_on
        if caller_phone and _feat_on(tenant_features, "patient_recognition"):
            patient_profile = await _load_patient_profile(caller_phone, hospital_id)
    except Exception:
        pass

    # ── Build system prompt ───────────────────────────────────────────────────
    from src.config.settings import settings
    # Per-hospital agent persona overrides the global env var default
    agent_name     = (getattr(hospital_ctx, "agent_name", None) or settings.AGENT_NAME)
    agent_language = (getattr(hospital_ctx, "agent_language", None) or settings.AGENT_LANGUAGE)
    system_prompt = _build_prompt(hospital_ctx, agent_name, outbound_context)
    if patient_profile:
        last = patient_profile["history"][0] if patient_profile["history"] else {}
        system_prompt += (
            f"\n\nRETURNING PATIENT: {patient_profile['name']} — "
            f"last seen {last.get('slot', 'recently')} with Dr. {last.get('doctor', '?')}. "
            "Greet them by name."
        )

    returning_name = patient_profile["name"] if (patient_profile and not outbound_context) else ""
    greeting = _build_greeting(hospital_ctx, agent_name, outbound_context, returning_name)

    # ── Tool set (tier baseline, then per-tenant feature gating) ───────────────
    from src.telephony.livekit_tools import ALL_TOOLS, CLINIC_TOOLS
    from src.tenancy.features import enabled as _feat_on
    tools = list(CLINIC_TOOLS if hospital_tier == "clinic" else ALL_TOOLS)
    def _tool_name(t) -> str:
        return getattr(t, "name", None) or getattr(t, "__name__", "")
    if not _feat_on(tenant_features, "multi_department_routing"):
        tools = [t for t in tools if _tool_name(t) != "transfer_to_department"]

    # ── Acoustic sensory layer ────────────────────────────────────────────────
    sensory = AcousticSensoryLayer()

    @ctx.room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        stream = rtc.AudioStream(track)

        async def _drain():
            async for frame in stream:
                sensory.feed(frame)

        asyncio.create_task(_drain())

    # ── Session userdata (accessible inside tools via context.userdata) ───────
    session_data = {
        "hospital_id":         hospital_id,
        "hospital_ctx":        hospital_ctx,
        "hospital_name":       hospital_name,
        "caller_phone":        caller_phone,
        "call_id":             call_id,
        "room_name":           room_name,
        "transfer_requested":  False,
        "transfer_destination": "",
    }

    # ── Start session ─────────────────────────────────────────────────────────
    agent = HospitalVoiceAgent(
        system_prompt=system_prompt,
        greeting=greeting,
        tools=tools,
        sensory=sensory,
        hospital_id=hospital_id,
        caller_phone=caller_phone,
        call_id=call_id,
        hospital_name=hospital_name,
        call_started_at=call_started_at,
        agent_language=agent_language,
        premium_llm=_feat_on(tenant_features, "premium_llm"),
        vad=ctx.proc.userdata.get("vad"),
    )

    # Groq free-tier TPM is small (12k). Disable preemptive generation (it fires
    # a second LLM call that our on_user_turn_completed mutation invalidates),
    # cap retries so a 429 doesn't hammer the same minute 4x, and limit tool
    # steps so a turn can't chain many large LLM calls.
    session = AgentSession(
        userdata=session_data,
        preemptive_generation=False,
        # Cut the post-speech wait before the LLM fires. Defaults are 0.5/6.0s;
        # 0.2/3.0 makes Arya feel near-realtime. max stays 3.0 so a slow speaker
        # who keeps talking past a pause still isn't cut off.
        min_endpointing_delay=0.2,
        max_endpointing_delay=3.0,
        max_tool_steps=2,
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=8.0),
        ),
    )

    # ── Post-call cleanup ─────────────────────────────────────────────────────
    async def _on_end_async(_event=None):
        try:
            ended_at = datetime.now(timezone.utc)
            total_turns = 0
            transcript: list[dict] = []
            try:
                msgs = session.history.messages()
                non_sys = [m for m in msgs if getattr(m, "role", "") != "system"]
                total_turns = len(non_sys) // 2
                for m in non_sys:
                    content = getattr(m, "content", "")
                    if isinstance(content, (list, tuple)):
                        content = " ".join(str(c) for c in content)
                    transcript.append({"role": getattr(m, "role", ""), "text": str(content)})
            except Exception:
                pass

            ud = session_data
            transfer_dest = ud.get("transfer_destination", "")
            if transfer_dest:
                print(f"[arteq] call ended — transfer to {transfer_dest}")

            try:
                from src.db.queries import write_call_log
                outcome = transfer_dest if transfer_dest else "completed"
                await write_call_log(
                    hospital_id=hospital_id,
                    call_id=call_id,
                    caller=caller_phone or "unknown",
                    started_at=call_started_at,
                    ended_at=ended_at,
                    total_turns=total_turns,
                    latency_avg_ms=0,
                    cost_paise=0,
                    transcript=transcript,
                    intents=[],
                    outcome=outcome,
                )
            except Exception as log_exc:
                print(f"[arteq] call log write failed: {log_exc}", file=sys.stderr)

            # Increment campaign answered counter if this was an outbound campaign call
            campaign_id = (outbound_context or {}).get("campaign_id", "")
            if campaign_id and total_turns > 0:
                try:
                    from src.db.queries import increment_campaign_calls_answered
                    await increment_campaign_calls_answered(campaign_id)
                except Exception as exc:
                    print(f"[arteq] campaign metric update failed: {exc}", file=sys.stderr)

            if getattr(settings, "POST_CALL_SMS_ENABLED", False) and caller_phone:
                from src.services.sms_service import SMSService
                await SMSService().send_call_summary(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    summary="Thank you for calling. Arya was happy to help.",
                )

            from src.services.staff_alert import StaffAlertService
            outcome_str = transfer_dest or "completed"
            await StaffAlertService().alert_call_summary(
                patient_phone=caller_phone or "unknown",
                turns=total_turns,
                outcome=outcome_str,
                summary="",
                call_id=call_id,
            )
        except Exception as exc:
            print(f"[arteq] post-call cleanup error: {exc}", file=sys.stderr)

    session.on("close", lambda e=None: asyncio.ensure_future(_on_end_async(e)))

    # record=False disables LiveKit Cloud OTLP telemetry export. The exporter
    # blocks on 10s TLS handshakes to the cloud observability endpoint and floods
    # logs with ReadTimeout tracebacks; we don't use cloud recording.
    await session.start(agent=agent, room=ctx.room, record=False)


def prewarm(proc) -> None:
    """Load the Silero VAD model once per worker process, before any call.

    Silero load is the heaviest per-call setup step; doing it here keeps it off
    the critical path so the first turn responds sooner.
    """
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.2)


if __name__ == "__main__":
    # LiveKit Cloud uses explicit dispatch. The token endpoint (src/main.py)
    # attaches RoomAgentDispatch(agent_name=LIVEKIT_DISPATCH_NAME) so this worker
    # joins the room on creation. Name MUST match the token side. Override
    # LIVEKIT_DISPATCH_NAME locally to isolate a dev worker from prod.
    from src.config.settings import settings as _settings
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        agent_name=_settings.LIVEKIT_DISPATCH_NAME,
    ))
