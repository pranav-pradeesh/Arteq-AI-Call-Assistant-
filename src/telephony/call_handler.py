"""
Call Handler — orchestrates every hospital call.

Pipeline: STT (Sarvam) → Groq Brain (LLaMA) → TTS (Sarvam)
"""
from __future__ import annotations

import asyncio
import dataclasses
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
    create_appointment,
    cancel_appointment_by_id,
    create_callback,
    get_appointments_by_phone,
    get_available_slots,
    get_all_opd_queue_estimates,
    get_dept_by_name_fuzzy,
    get_doctor_by_name_fuzzy,
    get_or_load_hospital_context,
    get_patient_profile,
    log_missed_question,
    write_call_log,
)
from src.services.staff_alert import StaffAlertService
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
STILL_THERE_PHRASE = "ഹലോ, ഇപ്പോഴും ഉണ്ടോ?"
TECH_PROBLEM_PHRASE = "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. ദയവായി ഒന്നൂടെ പറയാമോ?"
SERVICE_DOWN_PHRASE = (
    "ക്ഷമിക്കണം, ഈ service ഇപ്പോൾ ലഭ്യമല്ല. ദയവായി കുറച്ച് കഴിഞ്ഞ് വിളിക്കൂ."
)
HOLD_PHRASE = "ഒരു നിമിഷം, ഞാൻ connect ചെയ്യുന്നു…"


def common_warm_phrases() -> list[tuple[str, str]]:
    lang = settings.DEFAULT_LANGUAGE
    return [
        (CLARIFY_PHRASE, lang),
        (SPEAK_UP_PHRASE, lang),
        (STILL_THERE_PHRASE, lang),
        (TECH_PROBLEM_PHRASE, lang),
        (SERVICE_DOWN_PHRASE, lang),
        (HOLD_PHRASE, lang),
    ]


# DTMF digit → synthetic utterance (bypasses STT, goes straight to brain)
_DTMF_UTTERANCES = {
    "1": "OPD timing please",
    "2": "emergency",
    "3": "lab timings",
    "4": "pharmacy",
    "5": "billing inquiry",
    "0": "transfer to reception",
    "*": "repeat please",
    "#": "goodbye thank you",
}


class CallHandler:
    """Handles the full lifecycle of a single call."""

    def __init__(
        self,
        call_id: str,
        hospital_id: Optional[str] = None,
        caller_number: Optional[str] = None,
        tenant_slug: Optional[str] = None,
        outbound_context: Optional[dict] = None,
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
        self._alerts = StaffAlertService()
        self._consecutive_failures = 0
        self._call_dead = False
        self._transcript: list[dict] = []
        self._call_language = settings.DEFAULT_LANGUAGE
        self._empty_audio_streak = 0
        # Cache last spoken audio + text for repeat_last
        self._last_response_audio: bytes = b""
        self._last_response_text: str = ""
        self._last_response_lang: str = settings.DEFAULT_LANGUAGE
        # For outbound calls (confirmation/reminder/callback): context from Exotel CustomField
        self._outbound_context: Optional[dict] = outbound_context

    # ── Public interface ──────────────────────────────────────────────────────

    async def start_call(self) -> bytes:
        """Load hospital context, initialise brain, inject queue data, return greeting audio."""
        bind_call_context(call_id=self.call_id, tenant_id=self.hospital_id)
        logger.info("call_started", call_id=self.call_id, caller=self.caller_number)
        try:
            return await self._start_call_inner()
        except Exception as e:
            logger.error("start_call_unhandled", error=str(e), call_id=self.call_id)
            return await self._synthesize(SERVICE_DOWN_PHRASE) or b""

    async def _start_call_inner(self) -> bytes:
        """Inner start_call — raises on failure so start_call() can catch and respond."""
        try:
            ctx = await get_or_load_hospital_context(self.hospital_id)
        except Exception as e:
            logger.error("hospital_context_load_failed", error=str(e))
            return await self._synthesize(SERVICE_DOWN_PHRASE) or b""

        # Single query for all OPD queue counts (replaces N sequential per-dept calls)
        try:
            queue_data = await get_all_opd_queue_estimates(self.hospital_id)
            if queue_data:
                ctx = dataclasses.replace(ctx, queue_data=queue_data)
        except Exception:
            pass  # queue data is optional — never block a call for it

        self._ctx = ctx
        self._state = await create_state(
            call_id=self.call_id,
            tenant_id=self._ctx.hospital_id,
        )

        # Patient recognition — non-blocking, personalises the greeting
        patient_context = None
        if self.caller_number and getattr(settings, "PATIENT_RECOGNITION_ENABLED", True):
            try:
                patient_context = await get_patient_profile(
                    self.caller_number, self.hospital_id
                )
            except Exception:
                pass

        self._brain = GroqBrain(
            hospital_context=self._ctx,
            agent_name=settings.AGENT_NAME,
            patient_context=patient_context,
        )

        greeting_result = await self._brain.generate_greeting(
            outbound_context=self._outbound_context
        )
        self._transcript.append({"role": "assistant", "text": greeting_result.text})
        audio = await self._synthesize(greeting_result.text, language=greeting_result.language)
        if audio:
            self._last_response_audio = audio
            self._last_response_text = greeting_result.text
            self._last_response_lang = greeting_result.language or settings.DEFAULT_LANGUAGE
        return audio or b""

    async def process_audio_turn(self, audio_bytes: bytes) -> bytes:
        """One turn: audio in → audio out."""
        if not self._state or not self._ctx or not self._brain:
            return b""

        turn_start = time.monotonic()

        try:
            # ── STT ───────────────────────────────────────────────────────────
            stt_start = time.monotonic()
            stt_result = await self._stt.transcribe(audio_bytes, language="unknown")
            stt_ms = int((time.monotonic() - stt_start) * 1000)
            self._state.total_stt_ms += stt_ms

            logger.info("stt_result", transcript=stt_result.transcript[:100],
                        confidence=stt_result.confidence, latency_ms=stt_ms,
                        lang=stt_result.language_detected)

            if not stt_result.transcript.strip():
                self._empty_audio_streak += 1
                self._state.increment_clarification()
                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()
                await save_state(self._state)
                phrase = SPEAK_UP_PHRASE if self._empty_audio_streak >= 2 else CLARIFY_PHRASE
                return await self._synthesize(phrase, language=self._call_language)

            self._empty_audio_streak = 0

            if self._is_recording_announcement(stt_result.transcript):
                logger.info("recording_announcement_ignored",
                            transcript=stt_result.transcript[:80])
                return b""

            if self._looks_like_noise_or_greeting(stt_result.transcript):
                hosp_name = self._ctx.name_ml or self._ctx.name
                response_text = (
                    f"ഞാൻ {settings.AGENT_NAME}, {hosp_name}-ലെ AI receptionist. "
                    "Doctor timing, fees, department, appointment — എന്താ വേണ്ടേ, പറയൂ."
                )
                await save_state(self._state)
                return await self._synthesize(response_text)

            self._transcript.append({"role": "caller", "text": stt_result.transcript})

            # ── Sticky language ────────────────────────────────────────────────
            detected = stt_result.language_detected or self._call_language
            if len(stt_result.transcript.split()) >= 2 or \
                    self._call_language == settings.DEFAULT_LANGUAGE:
                self._call_language = detected
            lang = self._call_language

            return await self._run_brain_turn(stt_result.transcript, lang, turn_start)

        except Exception as e:
            logger.error("call_handler_error", error=str(e), call_id=self.call_id)
            return await self._synthesize(TECH_PROBLEM_PHRASE, language=self._call_language)

    async def process_text_turn(self, text: str) -> bytes:
        """Bypass STT — go directly to brain with a synthetic transcript.

        Used for DTMF digit mapping and dead-air silence prompts.
        """
        if not self._state or not self._ctx or not self._brain:
            return b""

        # Special-case: silence check prompt (injected by websocket_handler)
        if text == "(silence prompt)":
            return await self._synthesize(STILL_THERE_PHRASE, language=self._call_language)

        turn_start = time.monotonic()
        return await self._run_brain_turn(text, self._call_language, turn_start)

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

    # ── Core brain turn ───────────────────────────────────────────────────────

    async def _run_brain_turn(
        self, transcript: str, lang: str, turn_start: float
    ) -> bytes:
        """Run brain, dispatch side-effects, return TTS audio."""
        assert self._brain and self._state and self._ctx

        brain_result = await self._brain.process(
            transcript=transcript,
            language_detected=lang,
        )
        self._transcript.append({"role": "assistant", "text": brain_result.text})

        logger.info(
            "conversation",
            caller_said=transcript,
            arya_replied=brain_result.text,
            lang=brain_result.language,
            action_type=brain_result.action_type,
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
            action_type=brain_result.action_type,
            e2e_ms=e2e_ms,
        )

        # ── Repeat last response ───────────────────────────────────────────────
        if brain_result.repeat_requested and self._last_response_audio:
            return self._last_response_audio

        # ── SMS side-effects (fire-and-forget) ────────────────────────────────
        if brain_result.sms_type and brain_result.sms_type not in ("call_summary",) \
                and self.caller_number:
            asyncio.create_task(self._handle_sms(brain_result))

        # ── DB side-effects (fire-and-forget) ─────────────────────────────────
        if brain_result.action_type == "book_appointment":
            if brain_result.appointment_data.get("patient_name"):
                asyncio.create_task(self._persist_appointment(brain_result))

        elif brain_result.action_type == "cancel_appointment":
            asyncio.create_task(self._cancel_appointment(brain_result))

        elif brain_result.action_type == "request_callback":
            asyncio.create_task(self._persist_callback(brain_result))

        # ── Staff alert: emergency ─────────────────────────────────────────────
        if brain_result.is_emergency and self.caller_number:
            asyncio.create_task(self._alerts.alert_emergency(
                patient_phone=self.caller_number,
                transcript_snippet=transcript,
                call_id=self.call_id,
            ))

        # ── Missed question logging ────────────────────────────────────────────
        # Transfer to generic reception with no specific destination = Arya couldn't help
        if (brain_result.should_transfer
                and brain_result.transfer_destination in ("reception", "")
                and not brain_result.is_emergency
                and transcript):
            asyncio.create_task(self._log_missed_question(transcript, lang))

        # ── Call routing ──────────────────────────────────────────────────────
        if brain_result.should_end:
            audio = await self._synthesize(brain_result.text, language=brain_result.language)
            if brain_result.sms_type == "call_summary" and self.caller_number:
                asyncio.create_task(self._send_post_call_sms(brain_result))
            await self._end_call_gracefully()
            return audio or b""

        if brain_result.should_transfer or brain_result.is_emergency:
            self._state.transfer_requested = True
            # Speak hold phrase first, then the brain's text
            hold = await self._synthesize(HOLD_PHRASE, language=brain_result.language)
            brain_audio = await self._synthesize(brain_result.text, language=brain_result.language)
            audio = (hold or b"") + (brain_audio or b"")
            await save_state(self._state)
            return audio

        audio = await self._synthesize(brain_result.text, language=brain_result.language)
        await save_state(self._state)
        return audio or b""

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _synthesize(self, text: str, language: str = "ml-IN") -> bytes:
        if not text or self._call_dead:
            return b""
        audio = await self._tts.synthesize(text, language=language)
        if not audio:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 8:
                logger.error("circuit_break_tts", call_id=self.call_id)
                self._call_dead = True
                if self._state:
                    self._state.transfer_requested = True
            return b""
        # TTS recovered — clear failure streak
        self._consecutive_failures = 0
        self._last_response_audio = audio
        self._last_response_text = text
        self._last_response_lang = language
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
                data = result.appointment_data or result.sms_data or {}
                await self._sms.send_appointment_confirmation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    patient_name=data.get("patient_name", ""),
                    doctor_name=data.get("doctor_name", result.transfer_doctor),
                    date=data.get("date", ""),
                    time=data.get("time", ""),
                )
            elif result.sms_type == "appointment_cancel":
                data = result.appointment_data or {}
                await self._sms.send_appointment_cancellation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    patient_name=data.get("patient_name", ""),
                    doctor_name=data.get("doctor_name", ""),
                    date=data.get("date", ""),
                )
            elif result.sms_type == "callback_confirm":
                data = result.callback_data or {}
                await self._sms.send_callback_confirmation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    preferred_time=data.get("preferred_time", "soon"),
                )
            elif result.sms_type == "lab_schedule":
                data = result.sms_data or {}
                await self._sms.send_lab_schedule(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    test_name=data.get("test_name", ""),
                    instructions=data.get("instructions", "Follow your doctor's advice."),
                    lab_timing=data.get("timing", ""),
                )
        except Exception as exc:
            logger.error("sms_action_failed", error=str(exc), sms_type=result.sms_type)

    async def _persist_appointment(self, brain_result: GroqBrainResult) -> None:
        """Write a booked appointment to DB and fire confirmation SMS."""
        if not self._ctx:
            return
        data = brain_result.appointment_data
        try:
            doctor_id = None
            if data.get("doctor_name"):
                doc = await get_doctor_by_name_fuzzy(data["doctor_name"], self.hospital_id)
                if doc:
                    doctor_id = doc["id"]

            dept_id = None
            if data.get("dept"):
                dept = await get_dept_by_name_fuzzy(data["dept"], self.hospital_id)
                if dept:
                    dept_id = dept["id"]

            slot_dt = None
            if data.get("date") and data.get("time"):
                try:
                    naive = datetime.strptime(f"{data['date']} {data['time']}", "%Y-%m-%d %H:%M")
                    slot_dt = _INDIA_TZ.localize(naive)
                except Exception:
                    pass

            appt_id = await create_appointment(
                hospital_id=self.hospital_id,
                patient_name=data.get("patient_name", ""),
                patient_phone=self.caller_number or "",
                doctor_id=doctor_id,
                dept_id=dept_id,
                slot_time=slot_dt,
                notes=data.get("notes", ""),
                call_id=self.call_id,
            )
            logger.info("appointment_booked", appt_id=appt_id, call_id=self.call_id)

            asyncio.create_task(self._alerts.alert_new_booking(
                patient_name=data.get("patient_name", ""),
                patient_phone=self.caller_number or "",
                doctor_name=data.get("doctor_name", ""),
                date=data.get("date", ""),
                time=data.get("time", ""),
                call_id=self.call_id,
            ))

            if brain_result.sms_type == "appointment" and self.caller_number:
                await self._sms.send_appointment_confirmation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    patient_name=data.get("patient_name", ""),
                    doctor_name=data.get("doctor_name", ""),
                    date=data.get("date", ""),
                    time=data.get("time", ""),
                )
        except Exception as exc:
            logger.error("persist_appointment_failed", error=str(exc))

    async def _cancel_appointment(self, brain_result: GroqBrainResult) -> None:
        """Look up and cancel the most recent appointment for this caller."""
        if not self._ctx or not self.caller_number:
            return
        try:
            appts = await get_appointments_by_phone(self.caller_number, self.hospital_id)
            if appts:
                appt = appts[0]
                await cancel_appointment_by_id(str(appt["id"]), self.hospital_id)
                logger.info("appointment_cancelled", appt_id=str(appt["id"]))
                asyncio.create_task(self._alerts.alert_cancellation(
                    patient_name=appt.get("patient_name", ""),
                    patient_phone=self.caller_number or "",
                    doctor_name=appt.get("doctor_name", ""),
                    date=str(appt.get("slot_time", ""))[:10],
                    call_id=self.call_id,
                ))
                if brain_result.sms_type == "appointment_cancel":
                    data = brain_result.appointment_data or {}
                    await self._sms.send_appointment_cancellation(
                        phone=self.caller_number,
                        hospital_name=self._ctx.name,
                        patient_name=appt.get("patient_name", data.get("patient_name", "")),
                        doctor_name=appt.get("doctor_name", data.get("doctor_name", "")),
                        date=str(appt.get("slot_time", data.get("date", ""))),
                    )
        except Exception as exc:
            logger.error("cancel_appointment_failed", error=str(exc))

    async def _persist_callback(self, brain_result: GroqBrainResult) -> None:
        """Write callback request to DB and fire confirmation SMS."""
        if not self._ctx or not self.caller_number:
            return
        data = brain_result.callback_data
        try:
            cb_id = await create_callback(
                hospital_id=self.hospital_id,
                patient_phone=self.caller_number,
                patient_name="",
                reason=data.get("reason", ""),
                preferred_time=data.get("preferred_time", ""),
                call_id=self.call_id,
            )
            logger.info("callback_registered", cb_id=cb_id)
            if brain_result.sms_type == "callback_confirm":
                await self._sms.send_callback_confirmation(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    preferred_time=data.get("preferred_time", "soon"),
                )
        except Exception as exc:
            logger.error("persist_callback_failed", error=str(exc))

    async def _log_missed_question(self, question: str, language: str) -> None:
        """Log a question Arya couldn't answer so the hospital can improve the KB."""
        try:
            await log_missed_question(
                hospital_id=self.hospital_id,
                call_id=self.call_id,
                question=question,
                language=language,
            )
            await self._alerts.alert_missed_question(
                question=question,
                language=language,
                call_id=self.call_id,
            )
        except Exception as exc:
            logger.debug("log_missed_question_failed", error=str(exc))

    async def _send_post_call_sms(self, brain_result: GroqBrainResult) -> None:
        """Send a brief post-call summary SMS if enabled."""
        if not getattr(settings, "POST_CALL_SMS_ENABLED", False):
            return
        if not self.caller_number or not self._ctx:
            return
        try:
            data = brain_result.sms_data or {}
            summary = data.get("summary") or self._last_response_text[:120]
            if summary:
                await self._sms.send_call_summary(
                    phone=self.caller_number,
                    hospital_name=self._ctx.name,
                    summary=summary,
                )
        except Exception as exc:
            logger.error("post_call_sms_failed", error=str(exc))

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
