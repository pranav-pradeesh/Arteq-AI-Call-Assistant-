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

logger = get_logger(__name__)


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
        self._intent_engine = IntentEngine()
        self._knowledge = HospitalKnowledgeService(self._ctx)
        self._composer = ResponseComposer(
            hospital_name=self._ctx.name_ml or self._ctx.name
        )

        greeting = (
            f"നമസ്കാരം! {self._ctx.name_ml or self._ctx.name}-ലേക്ക് സ്വാഗതം. "
            f"എന്ത് സഹായം ആണ് വേണ്ടത്?"
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

            # ── Intent ───────────────────────────────────────────────────────
            intent_start = time.monotonic()
            intent_result = self._intent_engine.classify(
                stt_result.transcript,
                partial=stt_result.is_partial,
                prior_intent=self._state.last_intent,
            )
            self._state.update_from_result(intent_result)

            logger.info("intent_result", intent=intent_result.intent,
                        confidence=intent_result.confidence,
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
                logger.info("freeform_fallback", transcript=stt_result.transcript[:100])
                knowledge_result = self._knowledge.answer_freeform(
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
                "doctor_not_found", None,
            }
            if not knowledge_result.found and knowledge_result.missing in _no_answer_missing:
                logger.info("freeform_escalation", missing=knowledge_result.missing,
                            transcript=stt_result.transcript[:60])
                freeform = self._knowledge.answer_freeform(stt_result.transcript)
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

    async def _synthesize(self, text: str) -> bytes:
        if not text or self._call_dead:
            return b""
        audio = await self._tts.synthesize(text, language="ml-IN")
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
