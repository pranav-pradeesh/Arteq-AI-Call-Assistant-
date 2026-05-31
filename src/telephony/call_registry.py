"""
Call Registry — tracks active concurrent calls and enforces a cap.

Prevents resource exhaustion on the free Render tier when multiple
callers hit the same number simultaneously.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from src.observability.logger import get_logger

logger = get_logger(__name__)


class CallRegistry:
    """Tracks active calls and enforces a per-process concurrency cap."""

    def __init__(self, max_calls: int = 10) -> None:
        self._active: dict[str, float] = {}   # call_id → monotonic start time
        self._max = max_calls
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def max_calls(self) -> int:
        return self._max

    async def try_register(self, call_id: str) -> bool:
        """Claim a slot. Returns False (and does not register) if at capacity."""
        async with self._lock:
            if len(self._active) >= self._max:
                logger.warning(
                    "call_rejected_at_capacity",
                    active=len(self._active),
                    max=self._max,
                )
                return False
            self._active[call_id] = time.monotonic()
            logger.info("call_registered", call_id=call_id, active=len(self._active))
            return True

    async def unregister(self, call_id: str) -> None:
        """Release a slot. Safe to call even if the call was never registered."""
        async with self._lock:
            start = self._active.pop(call_id, None)
            if start is not None:
                duration_s = round(time.monotonic() - start, 1)
                logger.info(
                    "call_unregistered",
                    call_id=call_id,
                    duration_s=duration_s,
                    active=len(self._active),
                )

    def snapshot(self) -> dict:
        return {"active_calls": len(self._active), "max_calls": self._max}


# ── Process-level singleton ────────────────────────────────────────────────────

# Hard cap on simultaneous calls. Sized for the Render free tier (~512MB RAM,
# shared CPU) and Groq's 30 RPM free limit. 8 gives a hospital reception line
# enough headroom that callers rarely hit a busy signal, without overloading
# the instance. Bump to 20-30 after upgrading the Render plan and Groq tier.
MAX_CONCURRENT_CALLS = 8

_registry: Optional[CallRegistry] = None


def get_registry() -> CallRegistry:
    global _registry
    if _registry is None:
        _registry = CallRegistry(max_calls=MAX_CONCURRENT_CALLS)
    return _registry
