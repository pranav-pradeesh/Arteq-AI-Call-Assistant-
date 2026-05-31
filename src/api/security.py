"""
Authentication & rate limiting for internal / admin HTTP APIs.

These are reusable FastAPI dependencies meant to protect endpoints that
trigger privileged or paid actions (e.g. POST /api/v1/outbound/reminder,
admin/analytics routers). They are intentionally dependency-only and do not
modify any existing router — apply them via `dependencies=[...]`.

Settings are read defensively with getattr(settings, "KEY", default) so that
no change to settings.py is required to import this module.

Dependencies provided:
  - require_api_key : validates the "x-api-key" header (constant-time compare).
  - rate_limit(n)   : factory returning an in-memory fixed-window limiter
                      keyed by client IP + request path.

NOTE on scaling: the rate limiter is process-local. With multiple Uvicorn /
Gunicorn workers each worker keeps its own counters, so the effective limit is
roughly `max_per_minute * num_workers`. For a hard cross-worker / multi-host
limit, back this with Redis (the project already ships src/cache/redis_client.py)
and replace the in-memory `_WINDOWS` dict with INCR + EXPIRE on a key.
"""

from __future__ import annotations

import hmac
import threading
import time
from typing import Callable

from fastapi import HTTPException, Request, status

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger("api.security")


# ─────────────────────────────────────────────────────────────────────────────
# API key authentication
# ─────────────────────────────────────────────────────────────────────────────

_API_KEY_HEADER = "x-api-key"


def _is_production() -> bool:
    return getattr(settings, "ENV", "dev") == "production"


async def require_api_key(request: Request) -> None:
    """
    FastAPI dependency enforcing a shared internal API key.

    Behaviour:
      * INTERNAL_API_KEY unset + ENV == "production"  -> 503 (fail closed).
      * INTERNAL_API_KEY unset + non-production        -> allow (dev convenience).
      * INTERNAL_API_KEY set                           -> require exact, constant-time
                                                          match of the "x-api-key"
                                                          header, else 401.

    Usage:
        @router.post("/thing", dependencies=[Depends(require_api_key)])
    """
    configured_key = getattr(settings, "INTERNAL_API_KEY", "") or ""

    if not configured_key:
        if _is_production():
            logger.error("internal_api_key_missing_in_production")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API key not configured",
            )
        # Dev / local: allow through so the API is usable without a key.
        logger.warning("internal_api_key_unset_allowing_request_dev")
        return

    provided = request.headers.get(_API_KEY_HEADER, "") or ""

    # Constant-time comparison to avoid timing side channels.
    if not hmac.compare_digest(provided.encode("utf-8"), configured_key.encode("utf-8")):
        logger.warning(
            "internal_api_key_invalid",
            path=request.url.path,
            client=_client_ip(request),
            has_header=bool(provided),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": _API_KEY_HEADER},
        )

    return


# ─────────────────────────────────────────────────────────────────────────────
# In-memory fixed-window rate limiter
# ─────────────────────────────────────────────────────────────────────────────

# Maps "<client_ip>|<path>" -> (window_start_seconds, count_in_window).
# Guarded by a lock; FastAPI dependencies may run on a threadpool, so we keep
# this thread-safe rather than relying solely on the asyncio single-thread model.
_WINDOWS: dict[str, tuple[float, int]] = {}
_WINDOWS_LOCK = threading.Lock()

_WINDOW_SECONDS = 60.0


def _client_ip(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def _prune_expired(now: float) -> None:
    """Drop stale buckets so the dict does not grow unbounded over time."""
    stale = [
        key
        for key, (start, _count) in _WINDOWS.items()
        if now - start >= _WINDOW_SECONDS
    ]
    for key in stale:
        _WINDOWS.pop(key, None)


def rate_limit(max_per_minute: int) -> Callable[[Request], None]:
    """
    Build a FastAPI dependency that allows at most `max_per_minute` requests per
    (client IP + request path) per rolling 60s fixed window.

    Returns HTTP 429 (with a Retry-After header) once the limit is exceeded.

    Usage:
        @router.post("/thing", dependencies=[Depends(rate_limit(30))])
        # or at router level:
        APIRouter(dependencies=[Depends(rate_limit(30))])

    Process-local — see module docstring for the multi-worker / Redis caveat.
    """
    if max_per_minute <= 0:
        raise ValueError("max_per_minute must be a positive integer")

    async def _dependency(request: Request) -> None:
        key = f"{_client_ip(request)}|{request.url.path}"
        now = time.monotonic()

        with _WINDOWS_LOCK:
            # Opportunistic prune (cheap; bounded by number of active keys).
            if len(_WINDOWS) > 1024:
                _prune_expired(now)

            start, count = _WINDOWS.get(key, (now, 0))

            if now - start >= _WINDOW_SECONDS:
                # Window expired -> start a fresh window with this request.
                _WINDOWS[key] = (now, 1)
                return

            if count >= max_per_minute:
                retry_after = max(1, int(_WINDOW_SECONDS - (now - start)))
                logger.warning(
                    "rate_limit_exceeded",
                    path=request.url.path,
                    client=_client_ip(request),
                    limit=max_per_minute,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )

            _WINDOWS[key] = (start, count + 1)
            return

    return _dependency


__all__ = ["require_api_key", "rate_limit"]
