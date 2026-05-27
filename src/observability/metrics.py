"""
Prometheus metrics for latency tracking and call analytics.
All metrics are collected non-blocking via background tasks.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ─── Latency histograms ───────────────────────────────────────────────────────

STT_LATENCY = Histogram(
    "arteq_stt_latency_ms",
    "STT transcription latency in milliseconds",
    ["provider", "language"],
    buckets=[50, 100, 200, 300, 500, 1000, 2000],
)

INTENT_LATENCY = Histogram(
    "arteq_intent_latency_ms",
    "Intent classification latency in milliseconds",
    buckets=[1, 5, 10, 20, 50],
)

KNOWLEDGE_LATENCY = Histogram(
    "arteq_knowledge_latency_ms",
    "Knowledge service lookup latency in milliseconds",
    ["intent"],
    buckets=[1, 5, 10, 20, 50, 100],
)

TTS_LATENCY = Histogram(
    "arteq_tts_latency_ms",
    "TTS synthesis latency in milliseconds",
    ["provider"],
    buckets=[50, 100, 200, 300, 500, 1000, 2000],
)

END_TO_END_LATENCY = Histogram(
    "arteq_e2e_latency_ms",
    "End-to-end call turn latency (speech in → audio out)",
    ["tenant"],
    buckets=[200, 500, 1000, 1500, 2000, 3000, 5000],
)

# ─── Call counters ────────────────────────────────────────────────────────────

CALLS_TOTAL = Counter(
    "arteq_calls_total",
    "Total calls received",
    ["tenant"],
)

CALLS_ANSWERED = Counter(
    "arteq_calls_answered_total",
    "Calls answered successfully",
    ["tenant", "intent"],
)

CALLS_TRANSFERRED = Counter(
    "arteq_calls_transferred_total",
    "Calls transferred to human agent",
    ["tenant"],
)

CALLS_FAILED = Counter(
    "arteq_calls_failed_total",
    "Calls that ended with an error",
    ["tenant", "error_code"],
)

CLARIFICATIONS_TOTAL = Counter(
    "arteq_clarifications_total",
    "Number of clarification requests sent",
    ["tenant"],
)

# ─── Cache metrics ────────────────────────────────────────────────────────────

CACHE_HITS = Counter("arteq_cache_hits_total", "Cache hits", ["key_type"])
CACHE_MISSES = Counter("arteq_cache_misses_total", "Cache misses", ["key_type"])

# ─── Active calls gauge ──────────────────────────────────────────────────────

ACTIVE_CALLS = Gauge(
    "arteq_active_calls",
    "Number of calls currently in progress",
    ["tenant"],
)


def get_metrics_response() -> tuple[bytes, str]:
    """Return Prometheus metrics as bytes + content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
