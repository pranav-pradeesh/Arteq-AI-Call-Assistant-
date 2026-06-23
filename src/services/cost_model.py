"""
Per-service cost model — real usage × published per-unit rate.

There is no flat per-call price here. Each component is priced from the REAL
usage a call produced:

  • STT  (Sarvam Saarika)       audio seconds transcribed   × ₹/min
  • TTS  (Sarvam Bulbul)        characters synthesized      × ₹/1000 chars
  • LLM  (Gemini / OpenRouter)  real prompt/completion tokens × ₹/1M tokens
  • Telephony (Vobiz)           real per-call INR cost from the CDR API
                                (interim: duration × ₹/min until reconciled)

The published per-unit rates are the only static inputs — a provider's price
list — and every one is overridable by env so prices track changes without a
code edit. Everything they multiply is measured, not guessed. Sarvam and Gemini
do not expose a per-call cost API, so this (real usage × list price) is the
closest faithful figure; Vobiz and OpenRouter do, and those real numbers
override the estimate (see vobiz_billing.reconcile_cdr / OpenRouter generation).

All amounts are in paise (1 rupee = 100 paise).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _f(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except (TypeError, ValueError):
        return default


# ── Published rates (overridable via env) ─────────────────────────────────────
# Sarvam Saarika STT: ₹30 / audio-hour = 50 paise/min.
STT_PAISE_PER_MIN = _f("STT_PAISE_PER_MIN", 50.0)
# Sarvam Bulbul TTS: ₹0.30 / 1000 chars = 30 paise / 1000 chars.
TTS_PAISE_PER_KCHAR = _f("TTS_PAISE_PER_KCHAR", 30.0)
# Gemini 2.5-flash-lite list price ≈ $0.10/1M in, $0.40/1M out at ~₹85/$ →
# ~850 paise/1M input, ~3400 paise/1M output. Override per model/provider.
LLM_PAISE_PER_1M_INPUT = _f("LLM_PAISE_PER_1M_INPUT", 850.0)
LLM_PAISE_PER_1M_OUTPUT = _f("LLM_PAISE_PER_1M_OUTPUT", 3400.0)
# Vobiz outbound ≈ ₹0.70/min — interim only; the CDR job overwrites with the
# real billed INR cost once available.
TELEPHONY_PAISE_PER_MIN = _f("TELEPHONY_PAISE_PER_MIN", 70.0)


@dataclass
class CostBreakdown:
    stt_paise: int
    tts_paise: int
    llm_paise: int
    telephony_paise: int

    @property
    def cost_paise(self) -> int:
        return self.stt_paise + self.tts_paise + self.llm_paise + self.telephony_paise

    def as_dict(self) -> dict:
        d = asdict(self)
        d["cost_paise"] = self.cost_paise
        return d


def stt_paise(audio_seconds: float) -> int:
    return max(0, round(max(audio_seconds, 0.0) / 60.0 * STT_PAISE_PER_MIN))


def tts_paise(chars: int) -> int:
    return max(0, round(max(chars, 0) / 1000.0 * TTS_PAISE_PER_KCHAR))


def llm_paise(prompt_tokens: int, completion_tokens: int) -> int:
    inp = max(prompt_tokens, 0) / 1_000_000.0 * LLM_PAISE_PER_1M_INPUT
    out = max(completion_tokens, 0) / 1_000_000.0 * LLM_PAISE_PER_1M_OUTPUT
    return max(0, round(inp + out))


def telephony_paise_estimate(duration_s: float) -> int:
    return max(0, round(max(duration_s, 0.0) / 60.0 * TELEPHONY_PAISE_PER_MIN))


def call_cost_breakdown(
    *,
    duration_s: float,
    spoken_chars: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    telephony_override_paise: int | None = None,
) -> CostBreakdown:
    """Price one call from its real measured usage.

    telephony_override_paise: when the real Vobiz CDR cost is known, pass it to
    override the duration-based estimate. STT bills on the whole audio stream, so
    it uses call duration; TTS bills on characters Arya actually spoke; the LLM
    bills on real prompt/completion tokens.
    """
    return CostBreakdown(
        stt_paise=stt_paise(duration_s),
        tts_paise=tts_paise(spoken_chars),
        llm_paise=llm_paise(prompt_tokens, completion_tokens),
        telephony_paise=(
            telephony_paise_estimate(duration_s)
            if telephony_override_paise is None
            else max(0, int(telephony_override_paise))
        ),
    )
