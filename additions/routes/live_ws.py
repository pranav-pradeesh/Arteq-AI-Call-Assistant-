"""
backend-additions/routes/live_ws.py
===================================
WebSocket endpoint for real-time live-call monitoring.

    ws(s)://<host>/admin/ws/live?hospital_id=<uuid>&token=<jwt>

Flow:
  1. Validate the JWT passed as a query param (browsers can't set Authorization
     headers on a WebSocket handshake, so the dashboard sends the session token
     here).
  2. Send a `snapshot` of currently in-progress calls (same query as the
     polling endpoint in monitoring_api.py).
  3. Forward live events from the event bus (live_events.py) as they arrive.
  4. Send periodic `ping` frames as keepalive; close when the client goes away.

The dashboard hook (dashboard-next/src/lib/use-live-calls.ts) falls back to
polling /admin/hospitals/{id}/active-calls if this socket is unavailable, so
shipping/operating this endpoint is optional but recommended.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, List, Optional

import asyncpg
from jose import jwt, JWTError
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..live_events import subscribe_call_events

router = APIRouter(prefix="/admin", tags=["live"])

JWT_SECRET: str = os.environ.get("DASHBOARD_JWT_SECRET", "changeme")
JWT_ALGORITHM: str = "HS256"

_SNAPSHOT_SQL = """
    SELECT id, hospital_id, call_id, caller, started_at, ended_at,
           total_turns, latency_avg_ms, cost_paise, outcome, intents
    FROM call_logs
    WHERE hospital_id = $1
      AND started_at IS NOT NULL
      AND ended_at IS NULL
    ORDER BY started_at DESC
    LIMIT 50
"""


def _valid_token(token: str) -> bool:
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except JWTError:
        return False


async def _snapshot(pool: asyncpg.Pool, hospital_id: str) -> List[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SNAPSHOT_SQL, hospital_id)
    out: List[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k in ("id", "hospital_id", "call_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("started_at", "ended_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


@router.websocket("/ws/live")
async def ws_live(
    websocket: WebSocket,
    hospital_id: str = Query(..., description="Hospital UUID"),
    token: str = Query(..., description="Session JWT (sub + role claims)"),
) -> None:
    if not _valid_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Initial snapshot of in-progress calls (best-effort).
    pool: Optional[asyncpg.Pool] = getattr(websocket.app.state, "pool", None)
    if pool is not None:
        try:
            await websocket.send_json({"type": "snapshot", "calls": await _snapshot(pool, hospital_id)})
        except Exception:
            pass

    stop = asyncio.Event()

    async def forward() -> None:
        try:
            async for event in subscribe_call_events(hospital_id):
                await websocket.send_json(event)
        except Exception:
            stop.set()

    async def heartbeat() -> None:
        try:
            while not stop.is_set():
                await asyncio.sleep(25)
                await websocket.send_json({"type": "ping"})
        except Exception:
            stop.set()

    async def receiver() -> None:
        # We don't expect client messages; this exists to detect disconnects.
        try:
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, Exception):
            stop.set()

    tasks = [
        asyncio.create_task(forward()),
        asyncio.create_task(heartbeat()),
        asyncio.create_task(receiver()),
    ]
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            pass
