"""
backend-additions/live_events.py
================================
Live call event bus for real-time dashboard monitoring.

Two backends, chosen automatically:
  * Redis pub/sub  — when REDIS_URL is set. REQUIRED when the call loop runs in
    a different process from the web server (sip mode: the LiveKit worker
    publishes, the web server's WebSocket subscribes).
  * In-process     — fallback when REDIS_URL is absent. Works only when the
    publisher and the WebSocket live in the SAME process (stream mode, where
    the FastAPI app runs the voice loop itself).

The agent/voice loop publishes lifecycle events; the WebSocket endpoint
(routes/live_ws.py) subscribes and forwards them to connected browsers.

Event shapes (JSON):
  {"type": "call_started", "call": {...call_logs row...}}
  {"type": "call_updated", "call": {...}}
  {"type": "call_ended",   "call_id": "..."}
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from typing import AsyncIterator, Dict, Set

REDIS_URL = os.environ.get("REDIS_URL", "")
_CHANNEL = "arteq:live:{hid}"


class _InProcessBus:
    """Asyncio fan-out within a single process."""

    def __init__(self) -> None:
        self._subs: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, hospital_id: str, event: dict) -> None:
        for q in list(self._subs.get(hospital_id, ())):
            q.put_nowait(event)

    async def subscribe(self, hospital_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[hospital_id].add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[hospital_id].discard(q)
            if not self._subs[hospital_id]:
                self._subs.pop(hospital_id, None)


class _RedisBus:
    """Cross-process fan-out via Redis pub/sub (redis-py asyncio)."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis  # lazy import; only needed when REDIS_URL set

        self._redis = aioredis.from_url(url, decode_responses=True)

    async def publish(self, hospital_id: str, event: dict) -> None:
        await self._redis.publish(_CHANNEL.format(hid=hospital_id), json.dumps(event))

    async def subscribe(self, hospital_id: str) -> AsyncIterator[dict]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_CHANNEL.format(hid=hospital_id))
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    yield json.loads(msg["data"])
                except (ValueError, TypeError):
                    continue
        finally:
            await pubsub.unsubscribe(_CHANNEL.format(hid=hospital_id))
            await pubsub.aclose()


_bus = None


def get_bus():
    """Return the process-wide event bus (lazy singleton)."""
    global _bus
    if _bus is None:
        _bus = _RedisBus(REDIS_URL) if REDIS_URL else _InProcessBus()
    return _bus


# ── Public API ──────────────────────────────────────────────────────────────

async def publish_call_event(hospital_id: str, event: dict) -> None:
    """Publish a raw event dict to a hospital's live channel."""
    await get_bus().publish(hospital_id, event)


def subscribe_call_events(hospital_id: str) -> AsyncIterator[dict]:
    """Async iterator of events for a hospital (used by the WebSocket endpoint)."""
    return get_bus().subscribe(hospital_id)


# ── Convenience emitters for the agent / voice loop ─────────────────────────

async def emit_call_started(hospital_id: str, call: dict) -> None:
    await publish_call_event(hospital_id, {"type": "call_started", "call": call})


async def emit_call_updated(hospital_id: str, call: dict) -> None:
    await publish_call_event(hospital_id, {"type": "call_updated", "call": call})


async def emit_call_ended(hospital_id: str, call_id: str) -> None:
    await publish_call_event(hospital_id, {"type": "call_ended", "call_id": call_id})
