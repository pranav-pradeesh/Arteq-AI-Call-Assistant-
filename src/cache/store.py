"""
In-memory cache with TTL.

Redis is not configured, so we use a simple dict-based LRU cache.
For a multi-process deploy, swap this for Redis. For a single-process
(one uvicorn worker), this is correct and fast.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from src.config.settings import settings

@dataclass
class _Entry:
    value: Any
    expires_at: float


class MemoryCache:
    """Thread-safe enough for asyncio single-threaded event loop."""

    def __init__(self, max_size: int = 512):
        self._data: dict[str, _Entry] = {}
        self._max = max_size

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._data[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        if len(self._data) >= self._max:
            # Evict oldest
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            del self._data[oldest]
        self._data[key] = _Entry(value=value, expires_at=time.time() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        keys = [k for k in self._data if k.startswith(prefix)]
        for k in keys:
            del self._data[k]


# ── Singletons ────────────────────────────────────────────────────────────────

# Hospital context cache (long TTL — invalidated on dashboard update)
hospital_cache = MemoryCache(max_size=32)

# Call session state cache (short TTL — per active call)
session_cache = MemoryCache(max_size=256)

# TTS audio cache (long TTL — same text = same audio)
tts_cache = MemoryCache(max_size=settings.CACHE_MAX_SIZE)

HOSPITAL_CACHE_TTL = 300    # 5 min — reload hospital data
SESSION_TTL = settings.SESSION_TTL_S
TTS_CACHE_TTL = 86400       # 24 hrs for synthesized audio
