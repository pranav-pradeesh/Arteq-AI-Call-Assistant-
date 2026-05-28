"""
Call Handler — orchestrates every hospital call.

Pipeline: STT → Gemini Brain → TTS

One instance per call — stateful via ConversationState in memory.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import pytz

from src.ai.gemini_brain import GeminiBrain
from src.config.settings import settings
from src.conversation.state import (
    ConversationState,
    create_state,
    end_call,
    save_state,
)
from src.db.queries import (
    HospitalContext,
    get_or_load_hospital_context,
    write_call_log,
)
from src.observability.logger import get_logger, bind_call_context, clear_call_context
from src.stt.providers import CompositeSTT
from src.tts.engine import CompositeTTS

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")


class CallHandler:
    """Handles the full lifecycle of a single call."""

    def __init__(
        self,
        call_id: str,
        hospital_id: Optional[str] = None,
        caller_number: Optional[str] = None,
        tenant_slug: Optional[str] = None,
    ):
        self.call_id = call_id
        self.hospital_id = hospital_id or settings.HOSPITAL_ID
        self.caller_number = caller_number

        self._ctx: Optional[HospitalContext] = None
        self._state: Optional[ConversationState] = None
        self._brain: Optional[GeminiBrain] = None
        self._stt = CompositeSTT()
        self._tts = CompositeTTS()
        self._consecutive_failures = 0
        self._call_dead = False

    # ── Public interface ──────────────────────────────────────────────────────

    async def start_call(self) -> bytes:
        """Load hospital context, initialise Gemini brain, return greeting audio."""
        bind_call_context(call_id=self.call_id, tenant_id=self.hospital_id)
        logger.info("call_started", call_id=self.call_id, caller=self.caller_number)

        try:
            self._ctx = await get_or_load_hospital_context(self.hospital_id)
        except Exception as e:
            logger.error("hospital_context_load_failed", error=str(e))
            return await self._synthesize(
                "ക്ഷമിക്കണം, ഈ സേവനം ഇപ്പോൾ ലഭ്യമല്ല."
            ) or b""

        self._state = await create_state(
            call_id=self.call_id,
            tenant_id=self._ctx.hospital_id,
        )
        self._brain = GeminiBrain(
            hospital_context=self._ctx,
            agent_name=settings.AGENT_NAME,
        )

        greeting_result = await self._brain.generate_greeting()
        audio = await self._synthesize(
            greeting_result.text, language=greeting_result.language
        )
        return audio or b""

    async def process_audio_turn(self, audio_bytes: bytes) -> bytes:
        """One turn: audio in → audio out."""
        if not self._state or not self._ctx or not self._brain:
            return b""

        turn_start = time.monotonic()

        try:
            # ── STT ──────────────────────────────────────────────────────────
            stt_start = time.monotonic()
            stt_result = await self._stt.transcribe(audio_bytes, language="unknown")
            stt_ms = int((time.monotonic() - stt_start) * 1000)
            self._state.total_stt_ms += stt_ms

            logger.info("stt_result", transcript=stt_result.transcript[:100],
                        confidence=stt_result.confidence, latency_ms=stt_ms,
                        lang=stt_result.language_detected)

            # Empty transcript → ask to repeat
            if not stt_result.transcript.strip():
                self._state.increment_clarification()
                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()
                await save_state(self._state)
                return await self._synthesize(
                    "ക്ഷമിക്കണം, ശരിക്ക് കേൾക്കാൻ കഴിഞ്ഞില്ല. ഒന്നുകൂടി പറയാമോ?",
                )

            # Filter automated recording announcements (Google dialer etc.)
            if self._is_recording_announcement(stt_result.transcript):
                logger.info("recording_announcement_ignored",
                            transcript=stt_result.transcript[:80])
                return b""

            # Single-word backchannels ("hello", "hmm") — re-introduce the bot
            if self._looks_like_noise_or_greeting(stt_result.transcript):
                hosp_name = self._ctx.name_ml or self._ctx.name
                response_text = (
                    f"Hello! ഞാൻ {settings.AGENT_NAME} ആണ്, "
                    f"{hosp_name}-ലെ AI assistant. "
                    "Doctor timing, fees, departments, emergency — "
                    "എന്ത് സഹായം വേണം?"
                )
                await save_state(self._state)
                return await self._synthesize(response_text)

            # ── Gemini Brain ──────────────────────────────────────────────────
            lang = stt_result.language_detected or settings.DEFAULT_LANGUAGE
            brain_result = await self._brain.process(
                transcript=stt_result.transcript,
                language_detected=lang,
            )

            e2e_ms = int((time.monotonic() - turn_start) * 1000)
            logger.info("turn_complete",
                        response_preview=brain_result.text[:80],
                        lang=brain_result.language,
                        transfer=brain_result.should_transfer,
                        end=brain_result.should_end,
                        e2e_ms=e2e_ms)

            if brain_result.should_end:
                audio = await self._synthesize(
                    brain_result.text, language=brain_result.language
                )
                await self._end_call_gracefully()
                return audio

            if brain_result.should_transfer:
                self._state.transfer_requested = True
                audio = await self._synthesize(
                    brain_result.text, language=brain_result.language
                )
                await save_state(self._state)
                return audio

            await save_state(self._state)
            return await self._synthesize(
                brain_result.text, language=brain_result.language
            )

        except Exception as e:
            logger.error("call_handler_error", error=str(e), call_id=self.call_id)
            return await self._synthesize(
                "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. Staff-നോട് ബന്ധപ്പെടൂ."
            )

    async def end_call(self) -> None:
        try:
            if self._state:
                await end_call(self._state)
                asyncio.create_task(self._persist_call_log())
            await self._stt.close()
            await self._tts.close()
        except Exception as e:
            logger.error("end_call_error", error=str(e))
        finally:
            clear_call_context()

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _synthesize(self, text: str, language: str = "ml-IN") -> bytes:
        if not text or self._call_dead:
            return b""
        audio = await self._tts.synthesize(text, language=language)
        if not audio:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 5:
                logger.error("circuit_break_tts", call_id=self.call_id)
                self._call_dead = True
                if self._state:
                    self._state.transfer_requested = True
            return b""
        self._consecutive_failures = 0
        return audio

    async def _do_transfer(self) -> bytes:
        if self._state:
            self._state.transfer_requested = True
        phone = self._ctx.phone if self._ctx else ""
        if phone:
            msg = (f"ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. "
                   f"Hospital number: {phone}.")
        else:
            msg = "ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. ഒരു നിമിഷം."
        audio = await self._synthesize(msg)
        if self._state:
            await save_state(self._state)
        return audio

    async def _end_call_gracefully(self) -> None:
        await self.end_call()

    @staticmethod
    def _looks_like_noise_or_greeting(transcript: str) -> bool:
        t = transcript.lower().strip()
        _NOISE_WORDS = {
            "hello", "hi", "hey", "hm", "hmm", "mm", "um", "uh", "ah", "oh",
            "yeah", "yep", "yes", "no", "nope", "ok", "okay", "k",
            "helo", "haloo", "allo", "oi", "eh", "aye", "a",
            "hello?", "hi?",
        }
        return t in _NOISE_WORDS

    @staticmethod
    def _is_recording_announcement(transcript: str) -> bool:
        t = transcript.lower().strip()
        patterns = (
            "this call is being recorded",
            "call is being recorded",
            "call may be recorded",
            "being recorded for quality",
            "recorded for training",
            "recorded for quality",
            "this call may be monitored",
            "call may be monitored",
        )
        return any(p in t for p in patterns)

    async def _persist_call_log(self) -> None:
        if not self._state:
            return
        try:
            await write_call_log(
                hospital_id=self._ctx.hospital_id if self._ctx else self.hospital_id,
                call_id=self.call_id,
                caller=self.caller_number or "",
                started_at=datetime.fromtimestamp(
                    self._state.call_start_ts, tz=timezone.utc
                ),
                ended_at=datetime.now(timezone.utc),
                total_turns=self._state.turn_count,
                latency_avg_ms=int(
                    self._state.elapsed_ms() / max(self._state.turn_count, 1)
                ),
                cost_paise=0,
                transcript=[],
                intents=[],
                outcome="transferred" if self._state.transfer_requested else "answered",
            )
        except Exception as e:
            logger.error("persist_call_log_failed", error=str(e))
