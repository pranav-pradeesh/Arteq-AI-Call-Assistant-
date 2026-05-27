"""
Cache shim — backs the Redis API with in-memory store.

REDIS_URL is empty in this deployment, so we delegate to MemoryCache.
All function signatures are identical to the Redis version so no other
module needs to change.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from src.cache.store import (
    hospital_cache,
    session_cache,
    HOSPITAL_CACHE_TTL,
    SESSION_TTL,
)

_DEFAULT_TTL = 300


# ── Compatibility stub (health check calls get_redis().ping()) ───────────────

class _FakeRedis:
    async def ping(self) -> bool:
        return True


async def get_redis() -> _FakeRedis:
    return _FakeRedis()


async def close_redis() -> None:
    pass


# ── Generic helpers ───────────────────────────────────────────────────────────

async def cache_get(key: str) -> Optional[Any]:
    return session_cache.get(key)


async def cache_set(key: str, value: Any, ttl: int = _DEFAULT_TTL) -> bool:
    session_cache.set(key, value, ttl=ttl)
    return True


async def cache_delete(key: str) -> None:
    session_cache.delete(key)


async def cache_delete_pattern(pattern: str) -> int:
    prefix = pattern.rstrip("*")
    session_cache.delete_prefix(prefix)
    return 0


# ── Conversation state ────────────────────────────────────────────────────────

CALL_STATE_TTL = SESSION_TTL


async def get_call_state(call_id: str) -> Optional[dict]:
    return session_cache.get(f"call:{call_id}:state")


async def set_call_state(call_id: str, state: dict) -> bool:
    session_cache.set(f"call:{call_id}:state", state, ttl=CALL_STATE_TTL)
    return True


async def delete_call_state(call_id: str) -> None:
    session_cache.delete(f"call:{call_id}:state")


# ── Hospital/tenant config cache ──────────────────────────────────────────────

def tenant_config_key(slug: str) -> str:
    return f"tenant:{slug}:config"


async def get_tenant_config_cache(slug: str) -> Optional[dict]:
    return hospital_cache.get(tenant_config_key(slug))


async def set_tenant_config_cache(slug: str, config: dict) -> bool:
    hospital_cache.set(tenant_config_key(slug), config, ttl=HOSPITAL_CACHE_TTL)
    return True


async def invalidate_tenant_cache(slug: str) -> None:
    hospital_cache.delete(tenant_config_key(slug))
    hospital_cache.delete_prefix(f"branch:{slug}:")
