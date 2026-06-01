"""
Arteq Hospital Voice Agent — FastAPI entry point.

Routes:
  /ws/call/{tenant_slug}              WebSocket — Exotel audio stream
  /api/v1/call/inbound/{slug}         POST — Exotel call webhook (returns XML)
  /api/v1/outbound/reminder           POST — schedule outbound reminder call
  /api/v1/outbound/health             GET  — outbound service health
  /api/v1/call/status                 POST — Exotel call status callback
  /api/v1/health                      GET  — health check
  /metrics                            GET  — Prometheus metrics
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.config.settings import settings
from src.db.queries import close_pool, get_pool
from src.observability.logger import configure_logging, get_logger
from src.observability.metrics import get_metrics_response
from src.telephony.websocket_handler import handle_exotel_stream

logger = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    configure_logging()
    logger.info("arteq_starting", env=settings.ENV)

    # DB probe — best-effort, server starts regardless
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=15)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("db_connected")
    except asyncio.TimeoutError:
        logger.error("db_connection_timeout", hint="Check DATABASE_URL / network")
    except Exception as e:
        logger.error("db_connection_failed", error=str(e))

    # Pre-warm the TTS cache (greeting + common phrases) for instant playback
    try:
        from src.ai.groq_brain import build_greeting_text
        from src.db.queries import get_or_load_hospital_context
        from src.telephony.call_handler import common_warm_phrases
        from src.tts.engine import warm_tts_cache

        ctx = await get_or_load_hospital_context(settings.HOSPITAL_ID)
        hosp = ctx.name_ml or ctx.name
        lang = settings.DEFAULT_LANGUAGE
        # All three time-of-day greeting variants, so any call hour is instant.
        phrases = [
            (build_greeting_text(hosp, settings.AGENT_NAME, h), lang)
            for h in (8, 14, 20)
        ]
        phrases += common_warm_phrases()
        warmed = await warm_tts_cache(phrases)
        logger.info("tts_cache_warmed", count=warmed, total=len(phrases))
    except Exception as e:
        logger.warning("tts_warm_failed", error=str(e))

    # Reminder scheduler
    _scheduler_task = None
    try:
        from src.services.scheduler import start_scheduler
        _scheduler_task = start_scheduler()
    except Exception as e:
        logger.error("scheduler_start_failed", error=str(e))

    logger.info("arteq_ready", port=settings.PORT)
    yield

    # Shutdown scheduler before closing DB pool
    if _scheduler_task is not None:
        try:
            from src.services.scheduler import stop_scheduler
            await stop_scheduler(_scheduler_task)
        except Exception:
            pass

    await close_pool()
    logger.info("arteq_shutdown")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Arteq Hospital Voice Agent",
    description="Malayalam hospital enquiry voice system",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health_check():
    from src.telephony.call_registry import get_registry
    reg = get_registry()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "env": settings.ENV,
        "stt": settings.STT_PROVIDER,
        "tts": settings.TTS_PROVIDER,
        "hospital_id": settings.HOSPITAL_ID,
        "active_calls": reg.active_count,
        "max_calls": reg.max_calls,
    }


@app.get("/metrics")
async def metrics():
    from fastapi.responses import Response
    content, content_type = get_metrics_response()
    return Response(content=content, media_type=content_type)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/call/{tenant_slug}")
async def websocket_call_stream(websocket: WebSocket, tenant_slug: str):
    await handle_exotel_stream(websocket, tenant_slug)


# ── Exotel call webhook ───────────────────────────────────────────────────────

@app.post("/api/v1/call/inbound/{tenant_slug}")
async def call_inbound_webhook(tenant_slug: str, request: Request):
    """
    Exotel calls this URL when a call arrives.
    Returns XML that tells Exotel to open a WebSocket stream.
    """
    from fastapi.responses import Response
    ws_url = f"{settings.PUBLIC_WS_URL}/ws/call/{tenant_slug}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""
    return Response(content=xml, media_type="text/xml")


# ── Outbound calls & SMS ──────────────────────────────────────────────────────

try:
    from src.api.outbound import router as outbound_router, callback_router
    app.include_router(outbound_router)
    app.include_router(callback_router)
    logger.info("outbound_router_mounted")
except Exception as e:
    logger.error("outbound_router_mount_failed", error=str(e))


# ── Campaigns ─────────────────────────────────────────────────────────────────

try:
    from src.api.campaigns import router as campaign_router
    app.include_router(campaign_router)
    logger.info("campaign_router_mounted")
except Exception as e:
    logger.error("campaign_router_mount_failed", error=str(e))


# ── Admin Dashboard ───────────────────────────────────────────────────────────

try:
    from dashboard.routes.admin_api import router as admin_router
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    logger.info("dashboard_mounted", path="/admin")
except Exception as e:
    logger.error("dashboard_mount_failed", error=str(e))

# ── Legacy dashboard routes (optional, graceful skip if models missing) ───────

try:
    from dashboard.routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth-legacy"])
except Exception:
    pass
