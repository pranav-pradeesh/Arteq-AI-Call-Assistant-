"""
Call Handler — orchestrates every hospital call.

Pipeline per turn:  STT → Intent → Knowledge → TTS

5-level fallback:
  L1  Direct answer from DB data
  L2  Clarify once
  L3  Narrow the scope (offer choice)
  L4  Transfer to human
  L5  Graceful end
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import pytz

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
from src.intent.engine import IntentEngine, IntentResult
from src.intent.keywords import (
    INTENT_GOODBYE,
    INTENT_HUMAN_TRANSFER,
    INTENT_REPEAT,
    INTENT_SYMPTOM,
    INTENT_UNKNOWN,
)
from src.knowledge.service import HospitalKnowledgeService
from src.observability.logger import get_logger, bind_call_context, clear_call_context
from src.response.composer import (
    FALLBACK_MSG,
    ResponseComposer,
)
from src.stt.providers import CompositeSTT
from src.tts.engine import CompositeTTS
from src.ai.gemini_brain import GeminiBrain, BrainResult

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")


def _time_greeting() -> str:
    """Return a time-appropriate Malayalam greeting based on IST."""
    hour = datetime.now(_INDIA_TZ).hour
    if 5 <= hour < 12:
        return "സുപ്രഭാതം"       # Good Morning
    elif 12 <= hour < 17:
        return "ശുഭ ഉച്ചനേരം"   # Good Afternoon
    elif 17 <= hour < 21:
        return "ശുഭ സന്ധ്യ"      # Good Evening
    else:
        return "ശുഭ രാത്രി"       # Good Night


# Words that appear in department names but are not department identifiers.
_DEPT_NAME_SKIP_WORDS = frozenset({
    "dept", "department", "wing", "unit", "ward", "centre", "center",
    "section", "the", "and", "of", "for", "a", "an",
})


def _build_dept_keyword_rules(ctx: "HospitalContext") -> list[dict]:
    """
    Build per-call tenant keyword rules from the hospital's actual department
    names (both English name and Malayalam name_ml) stored in the DB.

    This lets the intent engine recognise hospital-specific branding like
    "NILA" (which is the hospital's gynecology ward name) even if "NILA"
    is not in the global DEPARTMENT_SYNONYMS dictionary.
    """
    from src.intent.keywords import INTENT_DOCTOR_AVAILABILITY
    rules: list[dict] = []
    for dept in ctx.departments:
        for raw_name in (dept.name, dept.name_ml):
            if not raw_name:
                continue
            name_lower = raw_name.lower().strip()
            # Register the full name
            rules.append({
                "keyword": name_lower,
                "maps_to_intent": INTENT_DOCTOR_AVAILABILITY,
                "weight": 2.0,
            })
            # Register each significant word individually
            for word in name_lower.split():
                word = word.strip(".,/()")
                if len(word) > 2 and word not in _DEPT_NAME_SKIP_WORDS:
                    rules.append({
                        "keyword": word,
                        "maps_to_intent": INTENT_DOCTOR_AVAILABILITY,
                        "weight": 1.5,
                    })
    return rules


class CallHandler:
    """
    Handles the full lifecycle of a single call.
    One instance per call — stateful via ConversationState in memory.
    """

    def __init__(
        self,
        call_id: str,
        hospital_id: Optional[str] = None,
        caller_number: Optional[str] = None,
        # backward compat alias
        tenant_slug: Optional[str] = None,
    ):
        self.call_id = call_id
        self.hospital_id = hospital_id or settings.HOSPITAL_ID
        self.caller_number = caller_number

        self._ctx: Optional[HospitalContext] = None
        self._state: Optional[ConversationState] = None
        self._intent_engine: Optional[IntentEngine] = None
        self._knowledge: Optional[HospitalKnowledgeService] = None
        self._composer: Optional[ResponseComposer] = None
        self._stt = CompositeSTT()
        self._tts = CompositeTTS()
        self._consecutive_failures = 0
        self._call_dead = False
        self._brain: Optional[GeminiBrain] = None

    # ── Public interface ──────────────────────────────────────────────────────

    async def start_call(self) -> bytes:
        """Load hospital context and return greeting audio."""
        bind_call_context(call_id=self.call_id, tenant_id=self.hospital_id)
        logger.info("call_started", call_id=self.call_id, caller=self.caller_number)

        try:
            self._ctx = await get_or_load_hospital_context(self.hospital_id)
        except Exception as e:
            logger.error("hospital_context_load_failed", error=str(e))
            return await self._tts.synthesize(
                "ക്ഷമിക്കണം, ഈ സേവനം ഇപ്പോൾ ലഭ്യമല്ല.", language="ml-IN"
            ) or b""

        self._state = await create_state(
            call_id=self.call_id,
            tenant_id=self._ctx.hospital_id,
        )
        self._intent_engine = IntentEngine(
            tenant_keyword_rules=_build_dept_keyword_rules(self._ctx)
        )
        self._knowledge = HospitalKnowledgeService(self._ctx)
        self._composer = ResponseComposer(
            hospital_name=self._ctx.name_ml or self._ctx.name
        )

        # Initialize Gemini brain if configured
        if settings.AI_BRAIN == "gemini":
            self._brain = GeminiBrain(
                hospital_context=self._ctx,
                agent_name=settings.AGENT_NAME,
            )

        time_greet = _time_greeting()
        hosp_name = self._ctx.name_ml or self._ctx.name
        greeting = (
            f"{time_greet}! Welcome to {hosp_name}. "
            f"ഞാൻ {settings.AGENT_NAME} ആണ്, ഇവിടത്തെ AI assistant. "
            f"Doctor timing, fees, departments — എന്ത് സഹായം വേണം?"
        )
        audio = await self._tts.synthesize(greeting, language="ml-IN")
        return audio or b""

    async def process_audio_turn(self, audio_bytes: bytes) -> bytes:
        """One turn: audio in → audio out."""
        if not self._state or not self._ctx:
            return b""

        turn_start = time.monotonic()
        response_text = ""

        try:
            # ── STT ──────────────────────────────────────────────────────────
            # Use auto-detect ("unknown") — callers mix English/Malayalam
            # ("op timing എപ്പോഴാ?"). Forcing ml-IN drops English words.
            stt_start = time.monotonic()
            stt_result = await self._stt.transcribe(audio_bytes, language="unknown")
            stt_ms = int((time.monotonic() - stt_start) * 1000)
            self._state.total_stt_ms += stt_ms

            logger.info("stt_result", transcript=stt_result.transcript[:100],
                        confidence=stt_result.confidence, latency_ms=stt_ms)

            if not stt_result.transcript.strip():
                self._state.increment_clarification()
                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()
                response_text = self._composer.clarification(
                    attempt=self._state.clarification_count - 1
                )
                await save_state(self._state)
                return await self._synthesize(response_text)

            # Filter automated recording announcements (Google dialer, IVR systems).
            # These are injected before the caller speaks; we silently discard them.
            if self._is_recording_announcement(stt_result.transcript):
                logger.info("recording_announcement_ignored",
                            transcript=stt_result.transcript[:80])
                return b""

            # ── Gemini Brain path (when AI_BRAIN=gemini) ─────────────────────
            if self._brain and self._brain.is_available():
                # Noise/greeting guard — don't feed backchannels to Gemini
                if self._looks_like_noise_or_greeting(stt_result.transcript):
                    hosp_name = self._ctx.name_ml or self._ctx.name if self._ctx else "ഈ hospital"
                    response_text = (
                        f"Hello! ഞാൻ {settings.AGENT_NAME} ആണ്, {hosp_name}-ലെ AI assistant. "
                        f"Doctor timing, fees, departments, emergency — "
                        f"എന്ത് സഹായം വേണം?"
                    )
                    await save_state(self._state)
                    return await self._synthesize(response_text, stt_result.language_detected or settings.DEFAULT_LANGUAGE)

                brain_result = await self._brain.process(
                    transcript=stt_result.transcript,
                    language_detected=stt_result.language_detected or settings.DEFAULT_LANGUAGE,
                )

                if brain_result.should_end:
                    audio = await self._synthesize(brain_result.text, brain_result.language)
                    await self._end_call_gracefully()
                    return audio

                if brain_result.should_transfer:
                    if self._state:
                        self._state.transfer_requested = True
                    audio = await self._synthesize(brain_result.text, brain_result.language)
                    if self._state:
                        await save_state(self._state)
                    return audio

                await save_state(self._state)
                return await self._synthesize(brain_result.text, brain_result.language)

            # ── Intent ───────────────────────────────────────────────────────
            intent_start = time.monotonic()
            intent_result = self._intent_engine.classify(
                stt_result.transcript,
                partial=stt_result.is_partial,
                prior_intent=self._state.last_intent,
            )
            self._state.update_from_result(intent_result)

            # Secondary dept scan: if the standard keyword index missed the
            # department (e.g. caller said "NILA" — the hospital's Malayalam ward
            # name that isn't in the global synonym list), scan the transcript
            # against the actual DB department names and name_ml values.
            if intent_result.entities.department is None and self._ctx.departments:
                transcript_lower = stt_result.transcript.lower()
                for dept in self._ctx.departments:
                    matched = False
                    for candidate in (dept.name, dept.name_ml):
                        if not candidate:
                            continue
                        cand_lower = candidate.lower()
                        if cand_lower in transcript_lower:
                            intent_result.entities.department = dept.name
                            matched = True
                            break
                        # Also check individual words of the dept name
                        for word in cand_lower.split():
                            word = word.strip(".,/()")
                            if (len(word) > 3
                                    and word not in _DEPT_NAME_SKIP_WORDS
                                    and word in transcript_lower):
                                intent_result.entities.department = dept.name
                                matched = True
                                break
                        if matched:
                            break
                    if matched:
                        break

            logger.info("intent_result", intent=intent_result.intent,
                        confidence=intent_result.confidence,
                        dept=intent_result.entities.department,
                        latency_ms=int((time.monotonic() - intent_start) * 1000))

            # Special intents
            if intent_result.intent == INTENT_GOODBYE:
                audio = await self._synthesize(self._composer.goodbye())
                await self._end_call_gracefully()
                return audio

            if intent_result.intent == INTENT_HUMAN_TRANSFER:
                return await self._do_transfer()

            if intent_result.intent == INTENT_REPEAT:
                response_text = self._composer.clarification(0)
                await save_state(self._state)
                return await self._synthesize(response_text)

            # Low confidence → don't clarify, hand to the LLM summary path
            # so the caller gets a real answer from hospital data instead of
            # cycling through "could you repeat?" → transfer.
            if intent_result.needs_clarification:
                # Noise / single-word greetings ("hello", "hi", "hmm") are not
                # real questions — sending them to Groq makes Groq ask
                # "ആരാണ് സംസാരിക്കുന്നത്?" (who is speaking?).
                # Re-introduce the bot instead so the caller knows what to ask.
                if self._looks_like_noise_or_greeting(stt_result.transcript):
                    hosp_name = self._ctx.name_ml or self._ctx.name if self._ctx else "ഈ hospital"
                    response_text = (
                        f"Hello! ഞാൻ {settings.AGENT_NAME} ആണ്, {hosp_name}-ലെ AI assistant. "
                        f"Doctor timing, fees, departments, emergency — "
                        f"എന്ത് സഹായം വേണം?"
                    )
                    await save_state(self._state)
                    return await self._synthesize(response_text)

                logger.info("freeform_fallback", transcript=stt_result.transcript[:100])
                knowledge_result = await self._knowledge.answer_freeform(
                    stt_result.transcript
                )
                response_text = knowledge_result.text_ml or self._composer.fallback()
                await save_state(self._state)
                return await self._synthesize(response_text)

            self._state.reset_clarification()

            # ── Knowledge ────────────────────────────────────────────────────
            entities_dict = {
                "department": intent_result.entities.department,
                "doctor_name": intent_result.entities.doctor_name,
                "day": intent_result.entities.day_reference,
                # Pass raw transcript for symptom → dept mapping
                "transcript": stt_result.transcript,
            }
            knowledge_result = self._knowledge.answer(
                intent=intent_result.intent,
                entities=entities_dict,
                state_context=self._state.to_dict(),
            )

            # Promote entities only when the lookup actually resolved.
            # If the dept/doctor wasn't found, clear so the next question
            # doesn't inherit "dentist" or some other dead entity.
            if knowledge_result.found:
                self._state.remember_resolved(
                    department=intent_result.entities.department,
                    doctor_name=intent_result.entities.doctor_name,
                )
            elif knowledge_result.missing in ("dept_not_found", "doctor_not_found"):
                self._state.clear_entity_context()

            # Freeform escalation: when structured path couldn't answer
            # (unsupported intent, or data not found and caller wasn't
            # just being vague), try the LLM with the full hospital summary.
            # This handles things like parking, insurance, facilities, etc.
            _no_answer_missing = {
                "unsupported_intent", "dept_not_found",
                "doctor_not_found", "no_symptom_match", None,
            }
            if not knowledge_result.found and knowledge_result.missing in _no_answer_missing:
                logger.info("freeform_escalation", missing=knowledge_result.missing,
                            transcript=stt_result.transcript[:60])
                freeform = await self._knowledge.answer_freeform(stt_result.transcript)
                if freeform.found and freeform.text_ml:
                    knowledge_result = freeform

            response_text = self._composer.compose(knowledge_result)

        except Exception as e:
            logger.error("call_handler_error", error=str(e), call_id=self.call_id)
            response_text = "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. Staff-നോട് ബന്ധപ്പെടൂ."

        e2e_ms = int((time.monotonic() - turn_start) * 1000)
        logger.info("turn_complete", response_preview=response_text[:80], e2e_ms=e2e_ms)

        await save_state(self._state)
        return await self._synthesize(response_text)

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
            msg = f"ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. Hospital number: {phone}."
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
        """
        True when the caller's utterance is ambient noise, a backchannel ("hmm"),
        or a one-word greeting ("hello", "hi") rather than a real question.
        These should NOT be sent to Groq because Groq responds with
        "ആരാണ് സംസാരിക്കുന്നത്?" (who's speaking?) — a receptionist reflex
        that makes the bot seem broken.
        """
        t = transcript.lower().strip()
        _NOISE_WORDS = {
            "hello", "hi", "hey", "hm", "hmm", "mm", "um", "uh", "ah", "oh",
            "yeah", "yep", "yes", "no", "nope", "ok", "okay", "k",
            "helo", "haloo", "allo", "oi", "eh", "aye", "a",
            # Malayalam single-word backchannels that get STT'd
            "hello?", "hi?",
        }
        return t in _NOISE_WORDS

    @staticmethod
    def _is_recording_announcement(transcript: str) -> bool:
        """
        Detect the automated "this call is being recorded" announcement that
        Google dialer and some IVR systems inject at the start of a call.
        Returns True when the utterance should be silently discarded.
        """
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

    def _narrowing_question(self, intent_result: IntentResult) -> str:
        if self._state and self._state.last_department:
            dept = self._state.last_department
            return f"{dept} doctor-ന്റെ timing ആണോ fee ആണോ അറിയേണ്ടത്?"
        return (
            "Doctor availability, timing, fee, അല്ലെങ്കിൽ emergency — "
            "ഏത് വിഷയത്തിൽ ആണ് സഹായം വേണ്ടത്?"
        )

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
                intents=[self._state.last_intent] if self._state.last_intent else [],
                outcome="transferred" if self._state.transfer_requested else "answered",
            )
        except Exception as e:
            logger.error("persist_call_log_failed", error=str(e))
