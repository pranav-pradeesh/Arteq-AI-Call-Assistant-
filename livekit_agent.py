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
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("livekit.agents")

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents import llm as agents_llm  # ChatContext, ChatMessage types
from livekit.plugins import openai, sarvam, silero

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
    logger as _sarvam_log,
)


class _BulbulV3ChunkedStream(_SarvamChunkedStream):
    async def _run(self, output_emitter) -> None:
        payload = {
            "target_language_code": self._opts.target_language_code,
            "text": self._input_text,
            "speaker": self._opts.speaker,
            "pace": self._opts.pace,
            "speech_sample_rate": self._opts.speech_sample_rate,
            "enable_preprocessing": self._opts.enable_preprocessing,
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
                for b64 in audios:
                    output_emitter.push(_b64.b64decode(b64))
        except asyncio.TimeoutError as e:
            raise _APITimeoutErr("Sarvam TTS API request timed out") from e
        except _aiohttp.ClientError as e:
            raise _APIConnErr(f"Sarvam TTS API connection error: {e}") from e


class BulbulV3TTS(_SarvamTTS):
    """Sarvam Bulbul v3 TTS — drops pitch/loudness, which v3 does not accept."""

    def synthesize(self, text: str, *, conn_options=None):
        from livekit.agents import DEFAULT_API_CONNECT_OPTIONS
        if conn_options is None:
            conn_options = DEFAULT_API_CONNECT_OPTIONS
        return _BulbulV3ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_CTX = 8   # keep system prompt + last 8 messages (4 turns) — Groq TPM is tight

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

async def _resolve_hospital_id(room_name: str) -> str:
    slug = room_name.split("-call-")[0] if "-call-" in room_name else room_name
    try:
        from src.db.queries import get_pool
        from src.config.settings import settings
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM hospitals WHERE "
                "slug=$1 OR LOWER(REPLACE(name,' ','-'))=$1 LIMIT 1",
                slug.lower(),
            )
        return str(row["id"]) if row else settings.HOSPITAL_ID
    except Exception as exc:
        print(f"[warn] hospital ID lookup failed: {exc}", file=sys.stderr)
        from src.config.settings import settings
        return settings.HOSPITAL_ID


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

STYLE: Reply in the caller's language (Malayalam default; also Hindi/Tamil/Kannada/Telugu/English/Manglish). Keep medical terms in English (OPD, ICU, scanning, casualty). Max 2 sentences, end with ONE question. Sound caring, not robotic.

If a [SENSORY:...] tag shows TENSION=TREMBLING or VOL/PITCH=LOW → patient may be in pain/frightened: speak very gently, reassure first.

EMERGENCY (chest pain, severe bleeding, unconscious, can't breathe, stroke, poisoning): call alert_emergency FIRST, say "Connecting you to emergency — please stay on the line."

DIGITS: 1=OPD/doctor 2=emergency 3=lab 4=pharmacy 5=billing 0=reception *=repeat

TOOLS: check_availability (is doctor free), book_appointment (collect name+doctor+date+time), reschedule_appointment, cancel_appointment, request_callback, get_doctor_schedule, get_department_info, send_location_sms, transfer_to_department.

AFTER HOURS: if CLOSED, give next opening and offer (a) book for then, (b) callback, or (c) emergency. Never say "closed, goodbye".
{outbound_block}
HOSPITAL:
{hosp_block}

TODAY: {day_name}, {time_str} IST | STATUS: {open_status}"""


def _build_greeting(hospital_ctx, agent_name: str, outbound_context: Optional[dict]) -> str:
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
        return build_greeting_text(hosp_name, settings.AGENT_NAME, hour)
    except Exception:
        return f"Namaste! {hosp_name}-ലേക്ക് സ്വാഗതം. ഞാൻ {agent_name}. എങ്ങനെ സഹായിക്കാം?"


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
    ) -> None:
        super().__init__(
            instructions=system_prompt,
            tools=tools,
            stt=sarvam.STT(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="saaras:v3",
                language="unknown",
            ),
            vad=silero.VAD.load(),
            llm=openai.LLM(
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY", ""),
                # llama3-70b-8192 is deprecated by Groq; 3.3-versatile is the
                # current 70B chat model. max_tokens is not accepted by
                # livekit-plugins-openai 1.1.7.
                model="llama-3.3-70b-versatile",
            ),
            tts=BulbulV3TTS(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="bulbul:v3",
                # Bulbul requires the target language code; without it the
                # request 400s and no audio is produced.
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

    # ── Hospital context ──────────────────────────────────────────────────────
    hospital_id   = await _resolve_hospital_id(room_name)
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
        if caller_phone:
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

    greeting = _build_greeting(hospital_ctx, agent_name, outbound_context)

    # ── Tool set (clinic tier gets a leaner set) ───────────────────────────────
    from src.telephony.livekit_tools import ALL_TOOLS, CLINIC_TOOLS
    tools = CLINIC_TOOLS if hospital_tier == "clinic" else ALL_TOOLS

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
    )

    session = AgentSession(userdata=session_data)

    # ── Post-call cleanup ─────────────────────────────────────────────────────
    async def _on_end_async(_event=None):
        try:
            ended_at = datetime.now(timezone.utc)
            total_turns = 0
            try:
                msgs = session.history.messages()
                non_sys = [m for m in msgs if getattr(m, "role", "") != "system"]
                total_turns = len(non_sys) // 2
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
                    transcript=[],
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

    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    # agent_name = "arya" → LiveKit Cloud uses explicit dispatch. The token
    # endpoint (src/main.py) attaches RoomAgentDispatch(agent_name="arya") so
    # this worker joins the room on creation.
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="arya"))
