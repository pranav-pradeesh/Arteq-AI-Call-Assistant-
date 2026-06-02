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
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents import llm as agents_llm  # ChatContext, ChatMessage types
from livekit.plugins import openai, sarvam, silero

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

_LLM_MAX_TOKENS = 600
_MAX_CTX = 20   # keep system prompt + last 20 messages (10 turns)

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

LANGUAGE:
- Auto-detect: Malayalam (default), Hindi, Tamil, Kannada, Telugu, English, Manglish
- Reply in the caller's language. Keep medical terms in English (OPD, ICU, appointment, scanning, casualty)
- Max 2 natural sentences. Always end with exactly ONE question.
- Never sound robotic. Sound like a caring, professional front-desk staff member.

ACOUSTIC CUES (from [SENSORY:...] tag if present):
- TENSION=TREMBLING or VOL=LOW, PITCH=LOW → patient may be in pain or frightened
  → speak VERY gently, reassure first, then help

EMERGENCIES — act immediately, no follow-up questions:
- Chest pain, severe bleeding, loss of consciousness, difficulty breathing, stroke, poisoning
- Call alert_emergency tool first. Say: "Connecting you to emergency — please stay on the line."

DIGIT MENU (when caller presses or says a digit):
1 = OPD timings / doctor schedule
2 = Emergency / casualty
3 = Laboratory / blood tests
4 = Pharmacy
5 = Billing / fees
0 = Reception desk
* = Please repeat

ACTIONS — use tools when:
- Caller asks if a doctor is FREE / wants open times → check_availability (before booking)
- Caller wants to BOOK appointment → collect name, doctor/dept, date, time → book_appointment
- Caller wants to RESCHEDULE / change time → reschedule_appointment
- Caller wants to CANCEL → cancel_appointment
- Caller wants a CALLBACK → request_callback
- Caller asks DOCTOR SCHEDULE → get_doctor_schedule
- Caller asks DEPARTMENT LOCATION → get_department_info
- Caller wants LOCATION SMS → send_location_sms
- Caller wants TRANSFER to dept → transfer_to_department

AFTER HOURS:
- If hospital is CLOSED and caller needs OPD: tell them next opening time
- Offer: (a) book for next opening, (b) callback when we open, or (c) emergency if urgent
- Never say "we're closed, goodbye" — always offer an alternative
{outbound_block}
HOSPITAL INFORMATION:
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
    ) -> None:
        super().__init__(
            instructions=system_prompt,
            tools=tools,
            stt=sarvam.STT(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="saaras:v3",
                language="unknown",
                mode="codemix",
            ),
            vad=silero.VAD.load(),
            llm=openai.LLM(
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY", ""),
                model="llama-3.3-70b-versatile",
                max_tokens=_LLM_MAX_TOKENS,
            ),
            tts=sarvam.TTS(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="bulbul:v3",
                target_language_code="ml-IN",
                speaker="shubh",
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
        """Speak the opening greeting when the call connects."""
        await self.session.generate_reply(
            instructions=f"Say exactly: {self._greeting!r}"
        )

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

        # Context pruning: keep system prompt + last _MAX_CTX messages
        try:
            msgs = turn_ctx.messages
            if len(msgs) > _MAX_CTX + 1:
                turn_ctx._messages = msgs[:1] + msgs[-_MAX_CTX:]
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
    print(f"[arteq] room={room_name} call_id={call_id[:8]}")

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
    agent_name    = settings.AGENT_NAME
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
    )

    session = AgentSession(userdata=session_data)

    # ── Post-call cleanup ─────────────────────────────────────────────────────
    async def _on_end_async(_event=None):
        try:
            ended_at = datetime.now(timezone.utc)
            total_turns = 0
            try:
                msgs = session.history.messages
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
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
