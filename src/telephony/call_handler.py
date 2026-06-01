"""
Call Handler — orchestrates every hospital call.

Pipeline: STT (Sarvam) → Groq Brain (LLaMA) → TTS (Sarvam)
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import pytz

from src.ai.groq_brain import GroqBrain, GroqBrainResult
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
from src.services.sms_service import SMSService
from src.stt.providers import CompositeSTT
from src.tts.engine import CompositeTTS

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")

# Fixed phrases reused across calls. Kept as constants so they can be
# pre-warmed into the TTS cache at startup (instant playback, no live TTS).
CLARIFY_PHRASE = "ക്ഷമിക്കണം, ശരിക്ക് കേട്ടില്ല. ഒന്നൂടെ പറയാമോ?"
SPEAK_UP_PHRASE = (
    "ക്ഷമിക്കണം, ശബ്ദം നന്നായി കേൾക്കുന്നില്ല. "
    "ഒന്നൂടെ, അൽപം ഉറക്കെ പറയാമോ?"
)
TECH_PROBLEM_PHRASE = "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. ദയവായി ഒന്നൂടെ പറയാമോ?"
SERVICE_DOWN_PHRASE = (
    "ക്ഷമിക്കണം, ഈ service ഇപ്പോൾ ലഭ്യമല്ല. ദയവായി കുറച്ച് കഴിഞ്ഞ് വിളിക്കൂ."
)

# All fixed phrases to pre-warm (text, language).
def common_warm_phrases() -> list[tuple[str, str]]:
    lang = settings.DEFAULT_LANGUAGE
    return [
        (CLARIFY_PHRASE, lang),
        (SPEAK_UP_PHRASE, lang),
        (TECH_PROBLEM_PHRASE, lang),
        (SERVICE_DOWN_PHRASE, lang),
    ]


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
        self._brain: Optional[GroqBrain] = None
        self._stt = CompositeSTT()
        self._tts = CompositeTTS()
        self._sms = SMSService()
        self._consecutive_failures = 0
        self._call_dead = False
        self._transcript: list[dict] = []
        # Sticky language for the call — stabilises native-language detection so
        # short/noisy clips don't flip the reply language mid-conversation.
        self._call_language = settings.DEFAULT_LANGUAGE
        self._empty_audio_streak = 0

    # ── Public interface ──────────────────────────────────────────────────────────

    async def start_call(self) -> bytes:
        """Load hospital context, initialise Groq brain, return greeting audio."""
        bind_call_context(call_id=self.call_id, tenant_id=self.hospital_id)
        logger.info("call_started", call_id=self.call_id, caller=self.caller_number)

        try:
            self._ctx = await get_or_load_hospital_context(self.hospital_id)
        except Exception as e:
            logger.error("hospital_context_load_failed", error=str(e))
            return await self._synthesize(SERVICE_DOWN_PHRASE) or b""

        self._state = await create_state(
            call_id=self.call_id,
            tenant_id=self._ctx.hospital_id,
        )
        self._brain = GroqBrain(
            hospital_context=self._ctx,
            agent_name=settings.AGENT_NAME,
        )

        greeting_result = await self._brain.generate_greeting()
        self._transcript.append({"role": "assistant", "text": greeting_result.text})
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

            # Empty transcript → audio was silent, too quiet, or garbled.
            # First miss: gently ask to repeat. Repeated misses: ask the caller
            # to speak up (line/range problem). Persistent: hand off to staff.
            if not stt_result.transcript.strip():
                self._empty_audio_streak += 1
                self._state.increment_clarification()
                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()
                await save_state(self._state)
                phrase = SPEAK_UP_PHRASE if self._empty_audio_streak >= 2 else CLARIFY_PHRASE
                return await self._synthesize(phrase, language=self._call_language)

            # Got a real transcript — reset the empty-audio streak.
            self._empty_audio_streak = 0

            # Filter automated recording announcements
            if self._is_recording_announcement(stt_result.transcript):
                logger.info("recording_announcement_ignored",
                            transcript=stt_result.transcript[:80])
                return b""

            # Single-word backchannels → re-introduce the bot
            if self._looks_like_noise_or_greeting(stt_result.transcript):
                hosp_name = self._ctx.name_ml or self._ctx.name
                response_text = (
                    f"ഞാൻ {settings.AGENT_NAME}, {hosp_name}-ലെ AI receptionist. "
                    "Doctor timing, fees, department, emergency — എന്താ വേണ്ടേ, പറയൂ."
                )
                await save_state(self._state)
                return await self._synthesize(response_text)

            self._transcript.append({"role": "caller", "text": stt_result.transcript})

            # ── Sticky language ────────────────────────────────────────────────
            # Update the call's language only on a confident (multi-word) clip,
            # OR while still on the default — so a one-word "mm/yes" on a noisy
            # line doesn't flip Malayalam → Tamil mid-call.
            detected = stt_result.language_detected or self._call_language
            if len(stt_result.transcript.split()) >= 2 or \
                    self._call_language == settings.DEFAULT_LANGUAGE:
                self._call_language = detected
            lang = self._call_language

            # ── Groq Brain ────────────────────────────────────────────────────
            brain_result = await self._brain.process(
                transcript=stt_result.transcript,
                language_detected=lang,
            )
            self._transcript.append({"role": "assistant", "text": brain_result.text})

            # Clean, full-text conversation line — grep "conversation" to read
            # the whole dialogue without the STT/metric noise.
            logger.info(
                "conversation",
                caller_said=stt_result.transcript,
                arya_replied=brain_result.text,
                lang=brain_result.language,
            )

            e2e_ms = int((time.monotonic() - turn_start) * 1000)
            logger.info(
                "turn_complete",
                response_preview=brain_result.text[:80],
                lang=brain_result.language,
                transfer=brain_result.should_transfer,
                end=brain_result.should_end,
                emergency=brain_result.is_emergency,
                dest=brain_result.transfer_destination,
                e2e_ms=e2e_ms,
            )

            # SMS actions triggered by brain (fire-and-forget)
            if brain_result.sms_type and self.caller_number:
                asyncio.create_task(self._handle_sms(brain_result))

            if brain_result.should_end:
                audio = await self._synthesize(
                    brain_result.text, language=brain_result.language
                )
                await self._end_call_gracefully()
                return audio

            if brain_result.should_transfer or brain_result.is_emergency:
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
            return await self._synthesize(TECH_PROBLEM_PHRASE, language=self._call_language)

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

    # ── Helpers ───────────────────────────────────────────────────────────────────

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

    async def _handle_sms(self, result: GroqBrainResult) -> None:
        """Fire-and-forget SMS based on brain routing decision."""
        if not self.caller_number or not self._ctx:
            return
        try:
            if result.sms_type == "maps":
                await self._sms.send_maps_link(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    address=self._ctx.address,
                )
            elif result.sms_type == "appointment":
                data = result.sms_data or {}
                await self._sms.send_appointment_confirmation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    patient_name=data.get("patient_name", ""),
                    doctor_name=data.get("doctor_name", result.transfer_doctor),
                    date=data.get("date", ""),
                    time=data.get("time", ""),
                )
            elif result.sms_type == "lab_schedule":
                data = result.sms_data or {}
                await self._sms.send_lab_schedule(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    test_name=data.get("test_name", ""),
                    instructions=data.get("instructions", "Please follow your doctor's advice."),
                    lab_timing=data.get("timing", ""),
                )
        except Exception as exc:
            logger.error("sms_action_failed", error=str(exc), sms_type=result.sms_type)

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
            outcome = "transferred" if self._state.transfer_requested else "answered"

            # Generate call summary if Groq is configured
            if self._transcript and settings.GROQ_API_KEY:
                try:
                    from src.services.call_summary import CallSummaryService
                    summary_svc = CallSummaryService()
                    summary = await summary_svc.generate(
                        conversation=self._transcript,
                        caller_number=self.caller_number or "",
                        hospital_name=self._ctx.name if self._ctx else "",
                        outcome=outcome,
                    )
                    await summary_svc.notify_staff(
                        summary=summary,
                        caller_number=self.caller_number or "",
                        hospital_id=self.hospital_id,
                    )
                except Exception as exc:
                    logger.error("call_summary_error", error=str(exc))

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
                transcript=self._transcript,
                intents=[],
                outcome=outcome,
            )
        except Exception as e:
            logger.error("persist_call_log_failed", error=str(e))
