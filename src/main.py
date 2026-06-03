"""
Arteq Hospital Voice Agent — FastAPI entry point.

Routes:
  /api/v1/call/inbound/{slug}         POST — Plivo inbound/answered-outbound webhook (PCML)
  /api/v1/outbound/reminder           POST — schedule outbound reminder call
  /api/v1/outbound/health             GET  — outbound service health
  /api/v1/call/status                 POST — Plivo call status callback
  /api/v1/livekit/token               GET  — LiveKit JWT for browser/mobile
  /api/v1/health                      GET  — health check
  /metrics                            GET  — Prometheus metrics
  /admin/*                            Admin dashboard API
"""
from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    from src.config.settings import settings
    from src.db.queries import close_pool, get_pool
    from src.observability.logger import configure_logging, get_logger
    from src.observability.metrics import get_metrics_response
except Exception as _import_exc:
    print(f"\n[ARTEQ FATAL] Import error at startup: {_import_exc}\n", file=sys.stderr, flush=True)
    raise

logger = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    configure_logging()
    logger.info("arteq_starting", env=settings.ENV)

    if "localhost" in settings.PUBLIC_BASE_URL or "localhost" in settings.PUBLIC_WS_URL:
        logger.warning(
            "misconfigured_public_urls",
            hint="Set PUBLIC_BASE_URL and PUBLIC_WS_URL to your Render service URL.",
            PUBLIC_BASE_URL=settings.PUBLIC_BASE_URL,
            PUBLIC_WS_URL=settings.PUBLIC_WS_URL,
        )
    else:
        logger.info("public_urls_ok",
                    base=settings.PUBLIC_BASE_URL, ws=settings.PUBLIC_WS_URL)

    # DB probe + idempotent schema migrations
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=15)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("db_connected")
        try:
            import pathlib
            migration_dir = pathlib.Path("migrations/versions")
            for sql_file in sorted(migration_dir.glob("*.sql")):
                sql = sql_file.read_text()
                async with pool.acquire() as conn:
                    await conn.execute(sql)
            logger.info("db_migrations_applied")
        except Exception as me:
            logger.warning("db_migration_warning", error=str(me))
    except asyncio.TimeoutError:
        logger.error("db_connection_timeout", hint="Check DATABASE_URL / network")
    except Exception as e:
        logger.error("db_connection_failed", error=str(e))

    # Reminder / confirmation / callback / follow-up scheduler
    _scheduler_task = None
    try:
        from src.services.scheduler import start_scheduler
        _scheduler_task = start_scheduler()
    except Exception as e:
        logger.error("scheduler_start_failed", error=str(e))

    logger.info("arteq_ready", port=settings.PORT)
    yield

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
    description="Malayalam hospital voice AI — LiveKit + Sarvam + Groq",
    version="2.0.0",
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


# ── Browser voice client ───────────────────────────────────────────────────────

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    _templates = Jinja2Templates(directory="dashboard/templates")
except Exception:
    _templates = None


@app.get("/", include_in_schema=False)
async def root():
    """Landing → the browser voice client so a tester can talk to the agent."""
    return RedirectResponse(url="/talk")


@app.get("/talk", response_class=HTMLResponse, include_in_schema=False)
async def talk(request: Request):
    """Serves the browser voice client (mic → LiveKit → agent)."""
    if _templates:
        return _templates.TemplateResponse("talk.html", {"request": request})
    return HTMLResponse("<h1>Voice client template not found</h1>", status_code=500)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "2.0.0",
        "env": settings.ENV,
        "hospital_id": settings.HOSPITAL_ID,
        "livekit_configured": bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY),
        "plivo_configured": bool(settings.PLIVO_AUTH_ID and settings.PLIVO_PHONE_NUMBER),
    }


@app.get("/metrics")
async def metrics():
    from fastapi.responses import Response
    content, content_type = get_metrics_response()
    return Response(content=content, media_type=content_type)


# ── LiveKit token endpoint ────────────────────────────────────────────────────

@app.get("/api/v1/livekit/token")
async def livekit_token(slug: str = "default", participant: str = "patient"):
    """Returns a LiveKit JWT so the browser can join a hospital room.

    Room name = "{slug}-call-{uuid}" (matches livekit_agent._resolve_hospital_id),
    so every call lands in a fresh room. The token embeds a RoomConfiguration
    with RoomAgentDispatch(agent_name="arya"); LiveKit Cloud uses that to
    dispatch the worker into the room on creation (Cloud Agents do NOT
    auto-dispatch — explicit dispatch via the token is required).
    """
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="LiveKit not configured")

    import uuid as _uuid
    room_name = f"{slug}-call-{_uuid.uuid4().hex[:12]}"

    try:
        from livekit.api import (
            AccessToken, VideoGrants, RoomConfiguration, RoomAgentDispatch,
        )
        token = (
            AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity(participant)
            .with_name(participant)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
            .with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name="arya")],
                )
            )
            .to_jwt()
        )
        return {"token": token, "room": room_name, "url": settings.LIVEKIT_URL}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")


# ── Plivo inbound webhook ─────────────────────────────────────────────────────

def _verify_plivo_signature(auth_token: str, full_url: str, params: dict, signature: str) -> bool:
    """
    Verify Plivo webhook signature per Plivo docs:
    HMAC-SHA1 over (url + sorted key=value pairs), base64-encoded.
    """
    import base64
    import hashlib
    import hmac as _hmac
    sorted_str = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    to_sign = (full_url + sorted_str).encode()
    expected = base64.b64encode(
        _hmac.new(auth_token.encode(), to_sign, hashlib.sha1).digest()
    ).decode()
    return _hmac.compare_digest(signature, expected)


@app.post("/api/v1/call/inbound/{tenant_slug}")
async def call_inbound_webhook(tenant_slug: str, request: Request):
    """
    Plivo calls this webhook for every answered inbound call.
    Returns PCML that SIP-forwards the call to LiveKit, where the dispatch
    rule creates a room named "{slug}-call-{uuid}" and the agent auto-joins.
    """
    from fastapi.responses import Response
    from src.services.livekit_sip import get_inbound_pcml

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    # Verify Plivo signature when auth token is configured
    if settings.PLIVO_AUTH_TOKEN:
        sig = request.headers.get("X-Plivo-Signature", "")
        full_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/inbound/{tenant_slug}"
        if sig and not _verify_plivo_signature(settings.PLIVO_AUTH_TOKEN, full_url, params, sig):
            logger.warning("plivo_signature_mismatch", tenant=tenant_slug)
            return Response(status_code=403)
        elif not sig:
            logger.debug("plivo_signature_absent", tenant=tenant_slug)

    to_number = params.get("To", settings.PLIVO_PHONE_NUMBER)
    xml = get_inbound_pcml(to_number=to_number)
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

try:
    from dashboard.routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
except Exception:
    pass
