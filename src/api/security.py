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
limit, back it with Redis (INCR + EXPIRE on a key) — there is no Redis client
in the project yet; `REDIS_URL` is currently only used by the live-event bus.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from typing import Callable, Mapping

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


# ─────────────────────────────────────────────────────────────────────────────
# Plivo webhook signature verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_plivo_signature_v1(
    auth_token: str, full_url: str, params: Mapping[str, str], signature: str
) -> bool:
    """Legacy X-Plivo-Signature: base64(HMAC-SHA1(url + sorted key+value pairs))."""
    sorted_str = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), (full_url + sorted_str).encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(signature, expected)


def verify_plivo_signature_v2(
    auth_token: str, full_url: str, nonce: str, signature: str
) -> bool:
    """X-Plivo-Signature-V2: base64(HMAC-SHA256(url + nonce))."""
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), (full_url + nonce).encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(signature, expected)


def plivo_webhook_authentic(
    request: Request, full_url: str, params: Mapping[str, str]
) -> bool:
    """True if the request carries a valid Plivo signature (V2 preferred, V1
    accepted), False otherwise — including when no signature header is present.
    Callers should skip the check entirely when PLIVO_AUTH_TOKEN is unset."""
    token = getattr(settings, "PLIVO_AUTH_TOKEN", "") or ""
    if not token:
        return True
    sig_v2 = request.headers.get("X-Plivo-Signature-V2", "")
    nonce = request.headers.get("X-Plivo-Signature-V2-Nonce", "")
    if sig_v2 and nonce:
        return verify_plivo_signature_v2(token, full_url, nonce, sig_v2)
    sig_v1 = request.headers.get("X-Plivo-Signature", "")
    if sig_v1:
        return verify_plivo_signature_v1(token, full_url, params, sig_v1)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Exotel webhook token verification
# ─────────────────────────────────────────────────────────────────────────────

def exotel_webhook_authentic(request: Request, url_token: str) -> bool:
    """Verify an Exotel inbound webhook.

    Exotel does not send a cryptographic signature header. Security relies on
    the webhook URL itself being a secret — `url_token` is a random token
    embedded in the URL path that only Exotel (who was given the configured URL)
    knows. If EXOTEL_WEBHOOK_TOKEN is blank the check is skipped (dev/test only).
    """
    expected = getattr(settings, "EXOTEL_WEBHOOK_TOKEN", "") or ""
    if not expected:
        return True
    return hmac.compare_digest(url_token, expected)


__all__ = [
    "require_api_key",
    "rate_limit",
    "plivo_webhook_authentic",
    "exotel_webhook_authentic",
    "verify_plivo_signature_v1",
    "verify_plivo_signature_v2",
]
