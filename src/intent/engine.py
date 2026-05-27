"""
Intent and entity extraction engine.

Algorithm:
  1. Tokenize transcript (split on whitespace + punctuation)
  2. For each token, score against all intent keyword lists — O(k)
  3. Accumulate weighted scores per intent
  4. Normalize scores
  5. Pick highest-scoring intent above threshold
  6. Extract entities from same token stream

Complexity: O(k * I) where k = tokens in transcript, I = number of intents
k is typically < 30 for a hospital query sentence.
I = 11 intents.
This is effectively constant-time for any realistic query.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.config.settings import settings
from src.intent.keywords import (
    ALL_INTENTS,
    DAY_KEYWORDS,
    INTENT_CONSULTATION_FEE,
    INTENT_CONTACT,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_EMERGENCY,
    INTENT_GOODBYE,
    INTENT_HOSPITAL_TIMING,
    INTENT_HUMAN_TRANSFER,
    INTENT_KEYWORDS,
    INTENT_LOCATION,
    INTENT_REPEAT,
    INTENT_UNKNOWN,
    resolve_day,
    resolve_department,
)


# ─────────────────────────────────────────────────────────────────────────────
# Output types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ExtractedEntities:
    department: Optional[str] = None       # canonical department name
    doctor_name: Optional[str] = None      # raw name as heard
    day_reference: Optional[str] = None    # "today", "monday", etc.
    fee_type: Optional[str] = None         # "consultation", "review"
    is_emergency: bool = False


@dataclass
class IntentResult:
    intent: str
    confidence: float
    needs_clarification: bool
    entities: ExtractedEntities = field(default_factory=ExtractedEntities)
    raw_scores: Dict[str, float] = field(default_factory=dict)
    processing_ms: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built keyword index for O(1) token lookup
# ─────────────────────────────────────────────────────────────────────────────
# Structure: {token: [(intent, weight), ...]}

_KEYWORD_INDEX: Dict[str, List[Tuple[str, float]]] = {}

for _intent, _kw_list in INTENT_KEYWORDS.items():
    for _kw, _weight in _kw_list:
        _kw_lower = _kw.lower()
        if _kw_lower not in _KEYWORD_INDEX:
            _KEYWORD_INDEX[_kw_lower] = []
        _KEYWORD_INDEX[_kw_lower].append((_intent, _weight))


# ─────────────────────────────────────────────────────────────────────────────
# Tokenizer
# ─────────────────────────────────────────────────────────────────────────────

# Strip everything except letters, digits, and spaces
_PUNCT_RE = re.compile(r"[^\w\sഀ-ൿ]")
# Split on whitespace
_SPLIT_RE = re.compile(r"\s+")


def tokenize(text: str) -> List[str]:
    """
    Fast tokenizer: lowercase, strip punctuation, split on whitespace.
    Also generates bigrams (two-word pairs) for multi-word keywords.
    """
    clean = _PUNCT_RE.sub(" ", text.lower())
    tokens = [t for t in _SPLIT_RE.split(clean) if len(t) > 1]
    # Add bigrams
    bigrams = [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


# ─────────────────────────────────────────────────────────────────────────────
# Main intent engine
# ─────────────────────────────────────────────────────────────────────────────


class IntentEngine:
    """
    Stateless keyword-based intent classifier.
    Thread-safe. Create once, reuse across calls.
    """

    def __init__(self, tenant_keyword_rules: Optional[List[dict]] = None):
        """
        tenant_keyword_rules: list of {keyword, maps_to_intent, maps_to_entity, weight}
        These are merged into the base index without modifying it.
        """
        self._base_index = _KEYWORD_INDEX
        self._tenant_overrides: Dict[str, List[Tuple[str, float]]] = {}

        if tenant_keyword_rules:
            for rule in tenant_keyword_rules:
                kw = rule["keyword"].lower()
                intent = rule["maps_to_intent"]
                weight = float(rule.get("weight", 1.0))
                if kw not in self._tenant_overrides:
                    self._tenant_overrides[kw] = []
                self._tenant_overrides[kw].append((intent, weight))

    def classify(
        self,
        transcript: str,
        partial: bool = False,
        prior_intent: Optional[str] = None,
    ) -> IntentResult:
        """
        Classify intent from transcript text.

        Args:
            transcript: raw STT output
            partial: if True, lower confidence threshold (partial utterance)
            prior_intent: intent from prior turn (context boost)

        Returns:
            IntentResult with intent, confidence, entities
        """
        t_start = time.monotonic()

        if not transcript or not transcript.strip():
            return IntentResult(
                intent=INTENT_UNKNOWN,
                confidence=0.0,
                needs_clarification=True,
            )

        tokens = tokenize(transcript)
        scores: Dict[str, float] = {intent: 0.0 for intent in ALL_INTENTS}

        # Score each token against keyword index
        for token in tokens:
            # Check base index
            if token in self._base_index:
                for intent, weight in self._base_index[token]:
                    scores[intent] += weight

            # Check tenant overrides (additive)
            if token in self._tenant_overrides:
                for intent, weight in self._tenant_overrides[token]:
                    scores[intent] += weight * 1.2  # slight boost for custom rules

        # Context boost: if prior intent is known, boost related intents slightly
        if prior_intent and prior_intent != INTENT_UNKNOWN:
            _boost_related_intents(scores, prior_intent)

        # Find top-scoring intent
        top_intent = max(scores, key=lambda k: scores[k])
        top_score = scores[top_intent]

        # If top score is 0, we have nothing
        if top_score == 0.0:
            result_intent = INTENT_UNKNOWN
            confidence = 0.0
        else:
            # Normalize: compare top score to second highest
            sorted_scores = sorted(scores.values(), reverse=True)
            second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

            # Confidence = how dominant the top intent is
            # High margin → high confidence
            if second_score == 0.0:
                confidence = min(top_score / 3.0, 1.0)
            else:
                margin = (top_score - second_score) / (top_score + second_score)
                # Scale to 0-1 with some normalization
                base_conf = min(top_score / 4.0, 0.9)  # raw score contribution
                margin_conf = margin * 0.4             # margin contribution
                confidence = min(base_conf + margin_conf, 1.0)

            result_intent = top_intent if confidence > 0.2 else INTENT_UNKNOWN

        # For partial utterances, lower the bar
        threshold = settings.INTENT_CONFIDENCE_THRESHOLD * (0.7 if partial else 1.0)
        needs_clarification = confidence < threshold

        # Entity extraction
        entities = _extract_entities(tokens, result_intent)

        processing_ms = int((time.monotonic() - t_start) * 1000)

        return IntentResult(
            intent=result_intent,
            confidence=round(confidence, 3),
            needs_clarification=needs_clarification,
            entities=entities,
            raw_scores={k: round(v, 3) for k, v in scores.items() if v > 0},
            processing_ms=processing_ms,
        )


def _boost_related_intents(scores: Dict[str, float], prior_intent: str) -> None:
    """
    Apply small context boosts based on conversational continuity.
    Example: if prior was doctor_availability, boost doctor_timing and consultation_fee.
    """
    RELATED_INTENTS = {
        INTENT_DOCTOR_AVAILABILITY: [INTENT_DOCTOR_TIMING, INTENT_CONSULTATION_FEE],
        INTENT_DOCTOR_TIMING: [INTENT_DOCTOR_AVAILABILITY, INTENT_CONSULTATION_FEE],
        INTENT_DEPARTMENT_EXISTS: [INTENT_DOCTOR_AVAILABILITY, INTENT_DOCTOR_TIMING],
        INTENT_CONSULTATION_FEE: [INTENT_DOCTOR_AVAILABILITY],
        INTENT_HOSPITAL_TIMING: [INTENT_EMERGENCY, INTENT_DOCTOR_TIMING],
        INTENT_EMERGENCY: [INTENT_CONTACT, INTENT_HOSPITAL_TIMING],
    }
    related = RELATED_INTENTS.get(prior_intent, [])
    for related_intent in related:
        scores[related_intent] = scores.get(related_intent, 0.0) + 0.3


def _extract_entities(tokens: List[str], intent: str) -> ExtractedEntities:
    """
    Extract structured entities from token list.
    O(k) pass over tokens.
    """
    entities = ExtractedEntities()

    # Department extraction — check each token against department synonyms
    for token in tokens:
        dept = resolve_department(token)
        if dept:
            entities.department = dept
            if dept == "emergency":
                entities.is_emergency = True
            break  # take first department match

    # Day reference extraction
    for token in tokens:
        day = resolve_day(token)
        if day:
            entities.day_reference = day
            break

    # Emergency flag
    emergency_tokens = {"emergency", "emergancy", "urgent", "accident", "casualty"}
    if any(t in emergency_tokens for t in tokens):
        entities.is_emergency = True

    # Fee type detection
    review_tokens = {"review", "followup", "follow-up", "follow up", "second", "revisit"}
    if any(t in review_tokens for t in tokens):
        entities.fee_type = "review"
    elif intent == INTENT_CONSULTATION_FEE:
        entities.fee_type = "consultation"

    return entities


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function for single-use classification
# ─────────────────────────────────────────────────────────────────────────────


def classify_intent(
    transcript: str,
    tenant_keyword_rules: Optional[List[dict]] = None,
    partial: bool = False,
    prior_intent: Optional[str] = None,
) -> IntentResult:
    """
    One-shot intent classification without caching the engine.
    For testing or simple use cases.
    For high-frequency use, instantiate IntentEngine once and reuse.
    """
    engine = IntentEngine(tenant_keyword_rules=tenant_keyword_rules)
    return engine.classify(transcript, partial=partial, prior_intent=prior_intent)
