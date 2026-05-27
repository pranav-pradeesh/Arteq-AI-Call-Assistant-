"""
Conversation state manager.

Keeps short-term rolling context per call:
  - Last known intent
  - Last mentioned department/doctor
  - Clarification attempt count
  - Call start time

Stored in Redis with TTL. Never persists to DB during the call.
Written to CallLog after call ends.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from src.cache.redis_client import delete_call_state, get_call_state, set_call_state
from src.intent.engine import IntentResult


@dataclass
class ConversationState:
    """
    Short-lived per-call context.
    Max size is O(1) — never grows with transcript length.
    """

    call_id: str
    tenant_id: str
    branch_id: Optional[str] = None

    # Short-term context (rolling window of last useful fact)
    last_intent: Optional[str] = None
    last_department: Optional[str] = None
    last_doctor_name: Optional[str] = None
    last_day_reference: Optional[str] = None

    # Clarification management
    clarification_count: int = 0
    last_clarification_text: Optional[str] = None

    # Call lifecycle
    call_start_ts: float = field(default_factory=time.time)
    turn_count: int = 0

    # Latency tracking (running totals ms)
    total_stt_ms: int = 0
    total_intent_ms: int = 0
    total_knowledge_ms: int = 0
    total_tts_ms: int = 0

    # Outcome tracking
    transfer_requested: bool = False
    call_ended: bool = False
    errors: list = field(default_factory=list)

    def update_from_result(self, intent_result: IntentResult) -> None:
        """
        Update turn-level context from a new intent classification.

        Entities are NOT promoted to last_department / last_doctor_name
        here — we wait until the knowledge layer confirms they actually
        resolved in this hospital. Otherwise a denied entity like
        'dentist' would leak into every follow-up question and silently
        reroute it through the missing department.
        """
        self.turn_count += 1
        self.total_intent_ms += intent_result.processing_ms

        if intent_result.intent and intent_result.intent != "unknown":
            self.last_intent = intent_result.intent

        # Day is safe to remember even if other things didn't resolve.
        if intent_result.entities.day_reference:
            self.last_day_reference = intent_result.entities.day_reference

    def remember_resolved(
        self,
        department: Optional[str] = None,
        doctor_name: Optional[str] = None,
    ) -> None:
        """Promote entities to rolling context only after a successful lookup."""
        if department:
            self.last_department = department
        if doctor_name:
            self.last_doctor_name = doctor_name

    def clear_entity_context(self) -> None:
        """Drop sticky entities (call this when last lookup failed)."""
        self.last_department = None
        self.last_doctor_name = None

    def increment_clarification(self) -> None:
        self.clarification_count += 1

    def reset_clarification(self) -> None:
        self.clarification_count = 0

    def should_transfer(self, max_clarifications: int) -> bool:
        return self.clarification_count >= max_clarifications

    def elapsed_ms(self) -> int:
        return int((time.time() - self.call_start_ts) * 1000)

    def add_error(self, error: dict) -> None:
        self.errors.append(error)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────────
# State manager (Redis-backed)
# ─────────────────────────────────────────────────────────────────────────────


async def load_state(call_id: str) -> Optional[ConversationState]:
    """Load state from Redis. Returns None if not found."""
    data = await get_call_state(call_id)
    if data is None:
        return None
    return ConversationState.from_dict(data)


async def save_state(state: ConversationState) -> None:
    """Persist state to Redis. Fire-and-forget (no await)."""
    await set_call_state(state.call_id, state.to_dict())


async def create_state(
    call_id: str,
    tenant_id: str,
    branch_id: Optional[str] = None,
) -> ConversationState:
    """Create fresh state for a new call."""
    state = ConversationState(
        call_id=call_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
    )
    await save_state(state)
    return state


async def end_call(state: ConversationState) -> None:
    """Mark call as ended and remove from Redis."""
    state.call_ended = True
    await delete_call_state(state.call_id)
