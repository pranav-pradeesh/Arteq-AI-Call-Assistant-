"""
Arteq Hospital Voice Agent — FastAPI Application Entry Point.

Routes:
  /ws/call/{tenant_slug}          WebSocket — audio streaming (production)
  /api/v1/call/inbound            POST — Exotel/Twilio call webhook
  /api/v1/health                  GET  — health check
  /metrics                        GET  — Prometheus metrics
  /dashboard/*                    Dashboard UI
  /api/v1/admin/*                 Admin REST API
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.cache.redis_client import close_redis, get_redis
from src.config.settings import settings
from src.db.connection import close_engine, get_engine
from src.db.models import Base
from src.observability.logger import configure_logging, get_logger
from src.observability.metrics import get_metrics_response
from src.telephony.websocket_handler import handle_exotel_stream, handle_twilio_stream

# Import dashboard routes
from dashboard.routes.hospitals import router as hospitals_router
from dashboard.routes.doctors import router as doctors_router
from dashboard.routes.departments import router as departments_router
from dashboard.routes.config import router as config_router
from dashboard.routes.auth import router as auth_router
from dashboard.routes.analytics import router as analytics_router

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    configure_logging()
    logger.info("arteq_starting", env=settings.APP_ENV)

    # Verify DB connection
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: c.execute(
                __import__("sqlalchemy").text("SELECT 1")
            ))
        logger.info("db_connected")
    except Exception as e:
        logger.error("db_connection_failed", error=str(e))

    # Verify Redis connection
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("redis_connected")
    except Exception as e:
        logger.error("redis_connection_failed", error=str(e))

    logger.info("arteq_ready", port=settings.PORT)
    yield

    # Shutdown
    await close_redis()
    await close_engine()
    logger.info("arteq_shutdown")


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="Arteq Hospital Voice Agent",
    description="Multi-tenant Malayalam hospital enquiry voice system",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else ["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
try:
    app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
except Exception:
    pass  # static dir may not exist yet


# ─────────────────────────────────────────────────────────────────────────────
# Core routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/api/v1/health")
async def health_check():
    """Health check for load balancer / uptime monitoring."""
    try:
        redis = await get_redis()
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {
        "status": "healthy" if redis_ok else "degraded",
        "version": "1.0.0",
        "env": settings.APP_ENV,
        "redis": "ok" if redis_ok else "error",
        "stt_provider": "sarvam" if settings.SARVAM_API_KEY else "deepgram",
        "tts_provider": "sarvam" if settings.SARVAM_API_KEY else "azure",
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    from fastapi.responses import Response
    content, content_type = get_metrics_response()
    return Response(content=content, media_type=content_type)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket audio streaming endpoint
# ─────────────────────────────────────────────────────────────────────────────


@app.websocket("/ws/call/{tenant_slug}")
async def websocket_call_stream(websocket: WebSocket, tenant_slug: str):
    """
    Primary WebSocket endpoint for audio streaming.
    Accepts connections from Exotel, Twilio, or custom providers.

    URL: wss://your-domain.com/ws/call/mother-hospital-thrissur
    """
    await handle_exotel_stream(websocket, tenant_slug)


@app.websocket("/ws/twilio/{tenant_slug}")
async def websocket_twilio_stream(websocket: WebSocket, tenant_slug: str):
    """Twilio Media Streams endpoint."""
    await handle_twilio_stream(websocket, tenant_slug)


# ─────────────────────────────────────────────────────────────────────────────
# Telephony webhooks (HTTP fallback for providers that use webhooks)
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/api/v1/call/inbound/{tenant_slug}")
async def call_inbound_webhook(tenant_slug: str, request: Request):
    """
    Inbound call webhook for Exotel.
    Returns TwiML/XML response to redirect to WebSocket stream.
    """
    from fastapi.responses import HTMLResponse
    base_url = str(request.base_url).rstrip("/")
    ws_url = f"{base_url.replace('http', 'ws')}/ws/call/{tenant_slug}"

    # Exotel-compatible response
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""
    return HTMLResponse(content=twiml, media_type="text/xml")


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard and admin API routes
# ─────────────────────────────────────────────────────────────────────────────

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(hospitals_router, prefix="/api/v1/admin/hospitals", tags=["hospitals"])
app.include_router(doctors_router, prefix="/api/v1/admin/doctors", tags=["doctors"])
app.include_router(departments_router, prefix="/api/v1/admin/departments", tags=["departments"])
app.include_router(config_router, prefix="/api/v1/admin/config", tags=["config"])
app.include_router(analytics_router, prefix="/api/v1/admin/analytics", tags=["analytics"])


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard UI (served as HTML)
# ─────────────────────────────────────────────────────────────────────────────

templates = Jinja2Templates(directory="dashboard/templates")


@app.get("/")
@app.get("/dashboard")
@app.get("/dashboard/{path:path}")
async def dashboard_ui(request: Request, path: str = ""):
    """Serve the dashboard SPA."""
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse({"message": "Dashboard loading...", "version": "1.0.0"})
