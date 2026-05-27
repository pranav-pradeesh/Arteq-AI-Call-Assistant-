"""
Call Handler — the central orchestrator for every hospital call.

Wires together:
  STT → Intent → Knowledge → Compose → TTS

Implements the 5-level fallback ladder:
  L1: Direct answer from structured data
  L2: Clarify once
  L3: Narrow the scope (offer limited choice)
  L4: Human transfer
  L5: Graceful end

Each turn target: < 1500ms end-to-end
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from src.config.settings import settings
from src.conversation.state import (
    ConversationState,
    create_state,
    end_call,
    load_state,
    save_state,
)
from src.intent.engine import IntentEngine, IntentResult
from src.intent.keywords import (
    INTENT_GOODBYE,
    INTENT_HUMAN_TRANSFER,
    INTENT_REPEAT,
    INTENT_UNKNOWN,
)
from src.knowledge.service import HospitalKnowledgeService
from src.observability.logger import (
    ErrorCode,
    bind_call_context,
    build_error_record,
    clear_call_context,
    get_logger,
)
from src.observability.metrics import (
    ACTIVE_CALLS,
    CALLS_ANSWERED,
    CALLS_FAILED,
    CALLS_TOTAL,
    CALLS_TRANSFERRED,
    CLARIFICATIONS_TOTAL,
    END_TO_END_LATENCY,
    INTENT_LATENCY,
    KNOWLEDGE_LATENCY,
    STT_LATENCY,
    TTS_LATENCY,
)
from src.response.composer import ResponseComposer
from src.stt.providers import CompositeSTT
from src.tenant.loader import TenantConfig, load_tenant_config
from src.tts.engine import CompositeTTS

logger = get_logger(__name__)


class CallHandler:
    """
    Handles the full lifecycle of a single call.

    Instantiate once per call. Stateful via ConversationState in Redis.
    Thread-safe (each call = independent instance).
    """

    def __init__(
        self,
        call_id: str,
        tenant_slug: str,
        caller_number: Optional[str] = None,
    ):
        self.call_id = call_id
        self.tenant_slug = tenant_slug
        self.caller_number = caller_number

        # Components (lazy-initialized)
        self._config: Optional[TenantConfig] = None
        self._state: Optional[ConversationState] = None
        self._intent_engine: Optional[IntentEngine] = None
        self._knowledge: Optional[HospitalKnowledgeService] = None
        self._composer: Optional[ResponseComposer] = None
        self._stt = CompositeSTT()
        self._tts = CompositeTTS()

    # ─────────────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────────────

    async def start_call(self) -> bytes:
        """
        Initialize call state and return greeting audio bytes.
        Called when incoming call is answered.
        """
        bind_call_context(self.call_id, self.tenant_slug)
        logger.info("call_started", caller=self.caller_number)

        # Load tenant config (cache-first, O(1) on hit)
        self._config = await load_tenant_config(self.tenant_slug)
        if not self._config or not self._config.is_active:
            logger.error("tenant_not_found", slug=self.tenant_slug)
            # Return a generic error greeting
            return await self._tts.synthesize(
                "ക്ഷമിക്കണം, ഈ സേവനം ഇപ്പോൾ ലഭ്യമല്ല.", language="ml-IN"
            ) or b""

        # Initialize components
        self._state = await create_state(
            call_id=self.call_id,
            tenant_id=self._config.tenant_id,
        )
        self._intent_engine = IntentEngine(
            tenant_keyword_rules=self._config.keyword_rules
        )
        self._knowledge = HospitalKnowledgeService(self._config)
        self._composer = ResponseComposer(
            hospital_name=self._config.name,
        )

        CALLS_TOTAL.labels(tenant=self.tenant_slug).inc()
        ACTIVE_CALLS.labels(tenant=self.tenant_slug).inc()

        # Greeting
        greeting_text = (
            self._config.greeting_text
            or f"നമസ്കാരം! {self._config.name}-ലേക്ക് സ്വാഗതം. എന്ത് സഹായം ആണ് വേണ്ടത്?"
        )
        audio = await self._tts.synthesize(greeting_text, language="ml-IN")
        return audio or b""

    async def process_audio_turn(self, audio_bytes: bytes) -> bytes:
        """
        Process one turn: audio in → audio response out.

        Pipeline:
          1. STT transcription
          2. Intent + entity extraction
          3. Knowledge retrieval
          4. Response composition
          5. TTS synthesis

        Returns audio bytes to stream back to caller.
        """
        if not self._state or not self._config:
            return b""

        turn_start = time.monotonic()
        response_text = ""

        try:
            # ── 1. STT ──────────────────────────────────────────────────────
            stt_start = time.monotonic()
            lang = self._config.stt_language_code or settings.SARVAM_STT_LANGUAGE
            stt_result = await self._stt.transcribe(audio_bytes, language=lang)
            stt_ms = int((time.monotonic() - stt_start) * 1000)

            STT_LATENCY.labels(
                provider=stt_result.provider, language=lang
            ).observe(stt_ms)
            self._state.total_stt_ms += stt_ms

            logger.info(
                "stt_result",
                transcript=stt_result.transcript[:100],
                confidence=stt_result.confidence,
                latency_ms=stt_ms,
            )

            # ── Handle STT failures ─────────────────────────────────────────
            if not stt_result.transcript.strip():
                self._state.increment_clarification()
                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()
                response_text = self._composer.clarification(
                    attempt=self._state.clarification_count - 1
                )
                await save_state(self._state)
                return await self._synthesize(response_text)

            if stt_result.confidence < settings.STT_CONFIDENCE_THRESHOLD:
                self._state.add_error(
                    build_error_record(
                        self.call_id, "stt", ErrorCode.STT_LOW_CONFIDENCE,
                        f"confidence={stt_result.confidence}",
                        severity="warning",
                    )
                )

            # ── 2. Intent extraction ────────────────────────────────────────
            intent_start = time.monotonic()
            intent_result = self._intent_engine.classify(
                stt_result.transcript,
                partial=(stt_result.is_partial),
                prior_intent=self._state.last_intent,
            )
            intent_ms = int((time.monotonic() - intent_start) * 1000)

            INTENT_LATENCY.observe(intent_ms)
            self._state.update_from_result(intent_result)

            logger.info(
                "intent_result",
                intent=intent_result.intent,
                confidence=intent_result.confidence,
                entities=str(intent_result.entities),
                latency_ms=intent_ms,
            )

            # ── Handle special intents before knowledge lookup ──────────────
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

            # ── 2b. Low intent confidence → clarify ─────────────────────────
            if intent_result.needs_clarification:
                self._state.increment_clarification()
                CLARIFICATIONS_TOTAL.labels(tenant=self.tenant_slug).inc()

                if self._state.should_transfer(settings.MAX_CLARIFICATION_ATTEMPTS):
                    return await self._do_transfer()

                # After first failure, try to narrow scope
                if self._state.clarification_count > 1:
                    response_text = self._compose_narrowing_question(intent_result)
                else:
                    response_text = self._composer.clarification(
                        attempt=self._state.clarification_count - 1
                    )

                await save_state(self._state)
                return await self._synthesize(response_text)

            # ── Reset clarification count on successful intent ───────────────
            self._state.reset_clarification()

            # ── 3. Knowledge retrieval ──────────────────────────────────────
            knowledge_start = time.monotonic()
            knowledge_result = self._knowledge.answer(
                intent_result,
                state_context=self._state.to_dict(),
            )
            knowledge_ms = int((time.monotonic() - knowledge_start) * 1000)

            KNOWLEDGE_LATENCY.labels(intent=intent_result.intent).observe(knowledge_ms)
            self._state.total_knowledge_ms += knowledge_ms

            # ── 4. Response composition ─────────────────────────────────────
            response_text = self._composer.compose(knowledge_result)

            CALLS_ANSWERED.labels(
                tenant=self.tenant_slug,
                intent=intent_result.intent,
            ).inc()

        except Exception as e:
            logger.error("call_handler_error", error=str(e), call_id=self.call_id)
            self._state.add_error(
                build_error_record(
                    self.call_id, "call_handler", "UNHANDLED_ERROR", str(e)
                )
            )
            CALLS_FAILED.labels(tenant=self.tenant_slug, error_code="UNHANDLED").inc()
            response_text = self._config.fallback_text or (
                "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. Staff-നോട് ബന്ധപ്പെടൂ."
            )

        # ── 5. TTS ─────────────────────────────────────────────────────────
        tts_start = time.monotonic()
        audio = await self._synthesize(response_text)
        tts_ms = int((time.monotonic() - tts_start) * 1000)
        TTS_LATENCY.labels(provider="composite").observe(tts_ms)

        # Record end-to-end latency
        e2e_ms = int((time.monotonic() - turn_start) * 1000)
        END_TO_END_LATENCY.labels(tenant=self.tenant_slug).observe(e2e_ms)

        logger.info(
            "turn_complete",
            response_preview=response_text[:80],
            e2e_ms=e2e_ms,
        )

        await save_state(self._state)
        return audio

    async def end_call(self) -> None:
        """Clean up call state and log to DB."""
        try:
            ACTIVE_CALLS.labels(tenant=self.tenant_slug).dec()
            if self._state:
                await end_call(self._state)
                await self._persist_call_log()
            await self._stt.close()
            await self._tts.close()
        except Exception as e:
            logger.error("end_call_error", error=str(e))
        finally:
            clear_call_context()

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _synthesize(self, text: str) -> bytes:
        """Synthesize text to audio, return empty bytes on failure."""
        if not text:
            return b""
        lang = "ml-IN"
        if self._config and self._config.stt_language_code:
            lang = self._config.stt_language_code
        audio = await self._tts.synthesize(text, language=lang)
        return audio or b""

    async def _do_transfer(self) -> bytes:
        """Transfer call to human agent."""
        if self._state:
            self._state.transfer_requested = True
        CALLS_TRANSFERRED.labels(tenant=self.tenant_slug).inc()

        transfer_number = self._config.transfer_number if self._config else None
        if transfer_number:
            # In a real telephony integration, signal SIP/Twilio/Exotel to
            # bridge the call to transfer_number. Here we prepare the audio.
            response_text = "ഞാൻ നിങ്ങളെ ഒരു staff member-ലേക്ക് connect ചെയ്യുന്നു. ഒരു നിമിഷം."
        else:
            response_text = (
                "ക്ഷമിക്കണം, ഞങ്ങളുടെ staff ഇപ്പോൾ available അല്ല. "
                "Hospital number-ൽ നേരിട്ട് ബന്ധപ്പെടൂ."
            )

        audio = await self._synthesize(response_text)
        if self._state:
            await save_state(self._state)
        return audio

    async def _end_call_gracefully(self) -> None:
        await self.end_call()

    def _compose_narrowing_question(self, intent_result: IntentResult) -> str:
        """
        After a failed clarification, offer a narrow choice.
        Example: "Doctor-ine kurich aano? Timings aano? Fee aano?"
        """
        if self._state and self._state.last_department:
            dept = self._state.last_department
            return f"{dept} doctor-ന്റെ timing ആണോ fee ആണോ അറിയേണ്ടത്?"

        return (
            "Doctor availability, timing, fee, അല്ലെങ്കിൽ emergency — "
            "ഏത് വിഷയത്തിൽ ആണ് സഹായം വേണ്ടത്?"
        )

    async def _persist_call_log(self) -> None:
        """
        Write call log to DB asynchronously.
        Non-blocking — scheduled as a background task.
        """
        if not self._state:
            return

        from src.db.connection import get_db_session
        from src.db.models import CallLog, CallOutcome
        import datetime

        async def _write():
            try:
                async with get_db_session() as session:
                    outcome = CallOutcome.TRANSFERRED if self._state.transfer_requested else CallOutcome.ANSWERED
                    log = CallLog(
                        call_id=self.call_id,
                        tenant_id=self._state.tenant_id or None,
                        caller_number=self.caller_number,
                        call_start=datetime.datetime.fromtimestamp(
                            self._state.call_start_ts,
                            tz=datetime.timezone.utc,
                        ),
                        call_end=datetime.datetime.now(datetime.timezone.utc),
                        duration_ms=self._state.elapsed_ms(),
                        detected_intent=self._state.last_intent,
                        outcome=outcome,
                        clarification_count=self._state.clarification_count,
                        transferred_to_human=self._state.transfer_requested,
                        stt_latency_ms=self._state.total_stt_ms,
                        intent_latency_ms=self._state.total_intent_ms,
                        knowledge_latency_ms=self._state.total_knowledge_ms,
                        tts_latency_ms=self._state.total_tts_ms,
                        errors_encountered=self._state.errors if self._state.errors else None,
                        stt_provider="sarvam",
                    )
                    session.add(log)
            except Exception as e:
                logger.error("call_log_write_error", error=str(e))

        asyncio.create_task(_write())
