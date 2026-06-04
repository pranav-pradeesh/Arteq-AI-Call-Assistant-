"""
In-memory cache with TTL.

Redis is not configured, so we use a simple dict-based LRU cache.
For a multi-process deploy, swap this for Redis. For a single-process
(one uvicorn worker), this is correct and fast.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from src.config.settings import settings

@dataclass
class _Entry:
    value: Any
    expires_at: float


class MemoryCache:
    """LRU + TTL cache. All hot-path ops are O(1).

    Backed by an OrderedDict so eviction pops the least-recently-used entry in
    O(1) (the previous min()-by-expiry scan was O(n) on every full set). Single
    asyncio thread, so no locking needed.
    """

    def __init__(self, max_size: int = 512):
        self._data: "OrderedDict[str, _Entry]" = OrderedDict()
        self._max = max_size

    def get(self, key: str) -> Optional[Any]:        # O(1)
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)                  # mark most-recently-used
        return entry.value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:   # O(1)
        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= self._max:
            self._data.popitem(last=False)           # evict LRU
        self._data[key] = _Entry(value=value, expires_at=time.time() + ttl)

    def delete(self, key: str) -> None:              # O(1)
        self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:    # O(n) — invalidation only
        for k in [k for k in self._data if k.startswith(prefix)]:
            del self._data[k]


# ── Singletons ────────────────────────────────────────────────────────────────

# Hospital context cache (long TTL — invalidated on dashboard update)
hospital_cache = MemoryCache(max_size=32)

# Tenant registry cache (slug -> tenant dict). On the pre-greeting critical
# path of every call; config changes rarely. Invalidated on dashboard writes.
tenant_cache = MemoryCache(max_size=128)
TENANT_CACHE_TTL = 300      # 5 min

# Call session state cache (short TTL — per active call)
session_cache = MemoryCache(max_size=256)

# TTS audio cache (long TTL — same text = same audio)
tts_cache = MemoryCache(max_size=settings.CACHE_MAX_SIZE)

HOSPITAL_CACHE_TTL = 300    # 5 min — reload hospital data
SESSION_TTL = settings.SESSION_TTL_S
TTS_CACHE_TTL = 86400       # 24 hrs for synthesized audio
