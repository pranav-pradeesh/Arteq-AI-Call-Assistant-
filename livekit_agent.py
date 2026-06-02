"""
Arteq Hospital Voice Agent — LiveKit edition.

Pipeline:  Silero VAD → Sarvam STT (Saaras v3) → Groq LLaMA 70B → Sarvam TTS (Bulbul v3)

Room naming: room name = hospital slug (e.g. "city-hospital").
The agent loads that hospital's context from the DB automatically, so one
worker pool serves every hospital — no code changes per client.

Usage:
  python livekit_agent.py dev      # local dev, connects to your LiveKit cloud room
  python livekit_agent.py start    # production worker pool mode

Required env vars:
  LIVEKIT_URL          wss://your-project.livekit.cloud
  LIVEKIT_API_KEY      from LiveKit dashboard
  LIVEKIT_API_SECRET   from LiveKit dashboard
  SARVAM_API_KEY
  GROQ_API_KEY
  DATABASE_URL         for hospital context loading
"""
from __future__ import annotations

import asyncio
import os
import sys

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import openai, sarvam, silero

load_dotenv()

# ── Sarvam SDK quirk: internal reasoning consumes the token budget silently.
# If max_tokens is too low, the LLM produces None content and the session
# crashes. Keep at 500+ minimum.
_LLM_MAX_TOKENS = 600

# Keep the last N messages in context — enough for a complete booking flow
# without blowing the Groq free-tier TPM cap (6 000 tokens / minute).
_MAX_CTX_MESSAGES = 20


# ==============================================================================
# Acoustic Sensory Layer
# Inspects raw PCM frames (delivered as rtc.AudioFrame) to detect patient
# emotional state: volume level, pitch proxy (zero-crossing rate), trembling.
# The metadata string is prepended to each transcript before the LLM sees it,
# giving the model a real-time emotional cue without any extra API calls.
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
            return "[SENSORY: STABLE]"
        avg_vol = np.mean(self._rms)
        avg_zcr = np.mean(self._zcr)
        vol_var = np.var(self._rms)
        zcr_var = np.var(self._zcr)

        vol = "HIGH" if avg_vol > 1500 else ("LOW" if avg_vol < 300 else "NORMAL")
        pit = "HIGH" if avg_zcr > 80 else ("LOW" if avg_zcr < 30 else "NORMAL")
        stb = "TREMBLING" if (zcr_var > 400 or vol_var > 50_000) else "STEADY"

        return f"[SENSORY: VOL={vol}, PITCH={pit}, TENSION={stb}]"


# ==============================================================================
# Hospital context loader
# Reuses the existing Arteq DB + cache infrastructure.
# Slug → hospital_id → full HospitalContext with departments/doctors/FAQs.
# ==============================================================================

async def _load_hospital_context(room_name: str):
    """
    Load HospitalContext for the given room name (= hospital slug).
    Falls back to settings.HOSPITAL_ID if the slug isn't found.
    """
    try:
        from src.db.queries import get_or_load_hospital_context, get_pool
        from src.config.settings import settings

        # Resolve slug → hospital_id via the DB
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM hospitals WHERE "
                "slug=$1 OR LOWER(REPLACE(name,' ','-'))=$1 LIMIT 1",
                room_name.lower(),
            )
        hospital_id = str(row["id"]) if row else settings.HOSPITAL_ID
        return await get_or_load_hospital_context(hospital_id)
    except Exception as exc:
        # DB not available in dev/test — return a minimal stub
        print(f"[warn] hospital context load failed ({exc}), using stub", file=sys.stderr)
        return None


def _make_system_prompt(ctx) -> str:
    """Build system prompt from HospitalContext, or return a generic stub."""
    if ctx is None:
        return (
            "You are Arya, a warm AI voice receptionist for an Arteq partner hospital. "
            "Reply concisely in 1-2 sentences. Speak Malayalam by default; "
            "switch language to match the caller. Always end with one question."
        )
    try:
        from src.ai.groq_brain import _build_system_prompt
        from src.config.settings import settings
        return _build_system_prompt(ctx, settings.AGENT_NAME)
    except Exception:
        return (
            f"You are Arya, warm AI receptionist for {ctx.name}. "
            "Reply in 1-2 sentences in the caller's language. End with one question."
        )


# ==============================================================================
# LiveKit Agent worker
# ==============================================================================

server = AgentServer()


@server.rtc_session()
async def session_handler(ctx: JobContext) -> None:
    """One LiveKit room = one hospital. Room name drives context loading."""
    await ctx.connect()
    print(f"[arteq] joined room: {ctx.room.name}")

    # Load hospital data (async, reuses the existing in-memory cache)
    hospital_ctx = await _load_hospital_context(ctx.room.name)
    system_prompt = _make_system_prompt(hospital_ctx)
    hospital_name = hospital_ctx.name if hospital_ctx else "Arteq Hospital"

    sensory = AcousticSensoryLayer()

    # ── Wire up raw audio frame capture for acoustic analysis ──────────────

    @ctx.room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        stream = rtc.AudioStream(track)

        async def _drain():
            async for frame in stream:
                sensory.feed(frame)

        asyncio.create_task(_drain())

    # ── Build the STT → LLM → TTS pipeline ────────────────────────────────

    vad = silero.VAD.load()

    stt = sarvam.STT(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        model="saaras:v3",
        language="unknown",     # auto-detect: Malayalam, Hindi, Tamil, Manglish …
        mode="codemix",
        flush_signal=True,
    )

    llm = openai.LLM(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.getenv("GROQ_API_KEY", ""),
        model="llama-3.3-70b-versatile",
        max_tokens=_LLM_MAX_TOKENS,
    )

    tts = sarvam.TTS(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        model="bulbul:v3",
        target_language_code="ml-IN",
        speaker="shubh",
    )

    session = AgentSession(vad=vad, stt=stt, llm=llm, tts=tts)
    agent = Agent(instructions=system_prompt)

    # ── Inject acoustic metadata before each LLM call ─────────────────────

    @session.on("user_speech_finished")
    def _on_speech(event):
        meta = sensory.metadata()
        sensory.reset()
        print(f"[arteq] acoustic: {meta}")

        # Prepend sensory state to transcript so the LLM can adapt tone
        event.text = f"{meta}\nPatient says: {event.text}"

        # Keep context lean — Groq free tier is 6 000 TPM
        if (
            hasattr(session, "chat_ctx")
            and hasattr(session.chat_ctx, "messages")
            and len(session.chat_ctx.messages) > _MAX_CTX_MESSAGES
        ):
            # Always keep message[0] (system prompt) + last N turns
            session.chat_ctx.messages = (
                session.chat_ctx.messages[:1]
                + session.chat_ctx.messages[-(_MAX_CTX_MESSAGES - 1):]
            )

    await session.start(agent=agent, room=ctx.room)

    # Opening greeting — time-aware, hospital-specific
    try:
        from src.ai.groq_brain import build_greeting_text
        from src.config.settings import settings
        from datetime import datetime
        import pytz
        hour = datetime.now(pytz.timezone("Asia/Kolkata")).hour
        greeting_text = build_greeting_text(hospital_name, settings.AGENT_NAME, hour)
    except Exception:
        greeting_text = f"Namaste, {hospital_name}-ലേക്ക് സ്വാഗതം. ഞാൻ Arya. എങ്ങനെ സഹായിക്കാം?"

    await session.generate_reply(instructions=f"Say exactly: {greeting_text!r}")


if __name__ == "__main__":
    cli.run_app(server)
