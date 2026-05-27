"""
Structured logging for all modules.

Uses structlog for structured JSON output in production,
human-readable in development.

All call-level events include call_id and tenant_id for tracing.
Logging is always async-safe — never blocks the call path.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from src.config.settings import settings


def configure_logging() -> None:
    """Configure structlog for the application. Called once at startup."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.ENV not in ("dev", "development", "local"):
        # JSON output for log aggregation (Datadog, CloudWatch, etc.)
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ]
        renderer = structlog.processors.JSONRenderer()
    else:
        # Human-readable for local development
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=True),
        ]
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )


def get_logger(name: str = "arteq") -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_call_context(call_id: str, tenant_id: str = "") -> None:
    """Bind call context to all subsequent log calls in this coroutine."""
    structlog.contextvars.bind_contextvars(
        call_id=call_id,
        tenant_id=tenant_id,
    )


def clear_call_context() -> None:
    structlog.contextvars.clear_contextvars()


# ─────────────────────────────────────────────────────────────────────────────
# Error schema (structured errors for observability)
# ─────────────────────────────────────────────────────────────────────────────


def build_error_record(
    call_id: str,
    module: str,
    error_code: str,
    message: str,
    severity: str = "error",
    **extra: Any,
) -> dict:
    """
    Build a structured error record.
    Stored in CallLog.errors_encountered and logged.
    """
    import time
    return {
        "call_id": call_id,
        "module": module,
        "error_code": error_code,
        "message": message,
        "severity": severity,
        "timestamp": time.time(),
        **extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Error categories
# ─────────────────────────────────────────────────────────────────────────────

class ErrorCode:
    # STT
    STT_TIMEOUT = "STT_TIMEOUT"
    STT_LOW_CONFIDENCE = "STT_LOW_CONFIDENCE"
    STT_NO_SPEECH = "STT_NO_SPEECH"
    STT_PROVIDER_ERROR = "STT_PROVIDER_ERROR"

    # Intent
    INTENT_UNKNOWN = "INTENT_UNKNOWN"
    INTENT_LOW_CONFIDENCE = "INTENT_LOW_CONFIDENCE"

    # Knowledge
    KNOWLEDGE_NO_DATA = "KNOWLEDGE_NO_DATA"
    KNOWLEDGE_CACHE_MISS = "KNOWLEDGE_CACHE_MISS"

    # Database
    DB_TIMEOUT = "DB_TIMEOUT"
    DB_CONNECTION_ERROR = "DB_CONNECTION_ERROR"

    # TTS
    TTS_TIMEOUT = "TTS_TIMEOUT"
    TTS_PROVIDER_ERROR = "TTS_PROVIDER_ERROR"

    # Telephony
    CALL_DROPPED = "CALL_DROPPED"
    AUDIO_STREAM_ERROR = "AUDIO_STREAM_ERROR"
    TRANSFER_FAILED = "TRANSFER_FAILED"

    # Tenant
    TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
    TENANT_INACTIVE = "TENANT_INACTIVE"
