"""
Redis cache layer.

Key naming convention:
  tenant:<slug>:config          → serialized TenantConfig (TTL: CACHE_TTL_SECONDS)
  tenant:<slug>:version         → config version number (TTL: none)
  branch:<branch_id>:full       → full branch data including depts/doctors
  call:<call_id>:state          → conversation state (TTL: 3600s / 1 hour)

O(1) access pattern for all hot paths.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as aioredis

from src.config.settings import settings

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return singleton Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=2,
            retry_on_timeout=True,
        )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


# ─── Generic helpers ──────────────────────────────────────────────────────────


async def cache_get(key: str) -> Optional[Any]:
    """
    Get a JSON-serialized value from cache.
    Returns None on cache miss or Redis error (fail-open).
    """
    try:
        client = await get_redis()
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        # Cache miss is acceptable — caller must handle it
        return None


async def cache_set(key: str, value: Any, ttl: int = settings.CACHE_TTL_SECONDS) -> bool:
    """
    Store a JSON-serializable value in cache with TTL.
    Returns True on success, False on error (fail-open).
    """
    try:
        client = await get_redis()
        serialized = json.dumps(value, default=str)
        await client.set(key, serialized, ex=ttl)
        return True
    except Exception:
        return False


async def cache_delete(key: str) -> None:
    """Delete a key. Used for cache invalidation after dashboard updates."""
    try:
        client = await get_redis()
        await client.delete(key)
    except Exception:
        pass


async def cache_delete_pattern(pattern: str) -> int:
    """
    Delete all keys matching pattern.
    Use sparingly — SCAN-based, not O(1).
    Returns count of deleted keys.
    """
    try:
        client = await get_redis()
        deleted = 0
        async for key in client.scan_iter(pattern, count=100):
            await client.delete(key)
            deleted += 1
        return deleted
    except Exception:
        return 0


# ─── Conversation state (short-lived call context) ────────────────────────────


CALL_STATE_TTL = 3600  # 1 hour max call lifetime


async def get_call_state(call_id: str) -> Optional[dict]:
    return await cache_get(f"call:{call_id}:state")


async def set_call_state(call_id: str, state: dict) -> bool:
    return await cache_set(f"call:{call_id}:state", state, ttl=CALL_STATE_TTL)


async def delete_call_state(call_id: str) -> None:
    await cache_delete(f"call:{call_id}:state")


# ─── Tenant config cache ──────────────────────────────────────────────────────


def tenant_config_key(slug: str) -> str:
    return f"tenant:{slug}:config"


async def get_tenant_config_cache(slug: str) -> Optional[dict]:
    return await cache_get(tenant_config_key(slug))


async def set_tenant_config_cache(slug: str, config: dict) -> bool:
    return await cache_set(tenant_config_key(slug), config, ttl=settings.CACHE_TTL_SECONDS)


async def invalidate_tenant_cache(slug: str) -> None:
    """Called when dashboard updates hospital data."""
    await cache_delete(tenant_config_key(slug))
    # Also clear all branch caches for this tenant
    await cache_delete_pattern(f"branch:*:{slug}:*")
