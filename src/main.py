"""
Arteq Hospital Voice Agent — FastAPI entry point.

Routes:
  /api/v1/livekit/token               GET  — LiveKit JWT for browser/mobile
  /api/v1/outbound/reminder           POST — schedule outbound reminder call
  /api/v1/outbound/health             GET  — outbound service health
  /api/v1/health                      GET  — health check
  /metrics                            GET  — Prometheus metrics
  /admin/*                            Admin dashboard API
  /api/v1/auth/*                      JWT auth endpoints
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

try:
    from src.config.settings import settings
    from src.db.queries import close_pool, get_control_pool as get_pool
    from src.observability.logger import configure_logging, get_logger
    from src.observability.metrics import get_metrics_response
except Exception as _import_exc:
    print(f"\n[ARTEQ FATAL] Import error at startup: {_import_exc}\n", file=sys.stderr, flush=True)
    raise

logger = get_logger(__name__)


class _MigrationError(RuntimeError):
    """A schema migration failed — startup must abort, not limp on."""


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
        # A failed migration means the schema is incomplete and queries will
        # break in confusing ways later — fail the startup, don't limp on.
        import pathlib
        migration_dir = pathlib.Path("migrations/versions")
        for sql_file in sorted(migration_dir.glob("*.sql")):
            sql = sql_file.read_text()
            try:
                async with pool.acquire() as conn:
                    await conn.execute(sql)
            except Exception as me:
                logger.error("db_migration_failed", file=sql_file.name, error=str(me))
                raise _MigrationError(f"Migration {sql_file.name} failed: {me}") from me
        logger.info("db_migrations_applied")

        # Upsert the superadmin account using the LIVE env password so the
        # credentials always match regardless of what hash is in 006b.sql.
        # Only runs when the users table exists (after migration 006).
        try:
            import bcrypt as _bcrypt
            _admin_email = settings.SUPERADMIN_EMAIL
            _admin_pw    = settings.DASHBOARD_ADMIN_PASSWORD or ""
            if _admin_pw:
                _hash = _bcrypt.hashpw(_admin_pw.encode()[:72], _bcrypt.gensalt(rounds=12)).decode()
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO users (email, password_hash, role, active)
                        VALUES ($1, $2, 'super_admin', true)
                        ON CONFLICT (email) DO UPDATE
                            SET password_hash = EXCLUDED.password_hash,
                                role          = 'super_admin',
                                active        = true
                        """,
                        _admin_email, _hash,
                    )
                logger.info("superadmin_upserted", email=_admin_email)
        except Exception as _se:
            logger.warning("superadmin_upsert_skipped", reason=str(_se))
    except asyncio.TimeoutError:
        logger.error("db_connection_timeout", hint="Check DATABASE_URL / network")
    except _MigrationError:
        raise  # abort startup — running on a half-migrated schema is worse
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
    description="Multilingual hospital voice AI — LiveKit + Sarvam + Google Gemini",
    version="2.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"]
if settings.ENV == "production" and _cors_origins == ["*"]:
    logger.warning(
        "cors_wildcard_in_production",
        hint="Set CORS_ORIGINS to your dashboard/app origins to restrict access.",
    )
# allow_credentials stays False: auth uses Bearer tokens, not cookies, so a
# wildcard origin never exposes credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
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
        "version": "2.1.0",
        "env": settings.ENV,
        "livekit_configured": bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY),
        "sarvam_configured": bool(settings.SARVAM_API_KEY),
        "gemini_configured": bool(settings.GOOGLE_API_KEY),
        "whatsapp_configured": bool(settings.WHATSAPP_ENABLED and settings.WHATSAPP_PHONE_NUMBER_ID),
        "vobiz_configured": bool(settings.VOBIZ_API_KEY and settings.VOBIZ_PHONE_NUMBER),
    }


@app.get("/metrics")
async def metrics():
    from fastapi.responses import Response
    content, content_type = get_metrics_response()
    return Response(content=content, media_type=content_type)


# ── LiveKit token endpoint ────────────────────────────────────────────────────

# Per-IP sliding-window guard so the unauthenticated token endpoint can't be
# abused to spin up unlimited agent rooms (each room costs STT/LLM/TTS spend).
_token_hits: dict[str, list[float]] = {}


def _token_rate_ok(client_ip: str) -> bool:
    import time
    now = time.monotonic()
    window = settings.TOKEN_RATE_WINDOW_SECONDS
    hits = [t for t in _token_hits.get(client_ip, []) if now - t < window]
    if len(hits) >= settings.TOKEN_RATE_LIMIT:
        _token_hits[client_ip] = hits
        return False
    hits.append(now)
    _token_hits[client_ip] = hits
    # Opportunistic cleanup so the dict can't grow unbounded.
    if len(_token_hits) > 10000:
        for k in [k for k, v in _token_hits.items()
                  if not any(now - t < window for t in v)]:
            _token_hits.pop(k, None)
    return True


@app.get("/api/v1/livekit/token")
async def livekit_token(request: Request, slug: str = "default", participant: str = "patient"):
    """Returns a LiveKit JWT so the browser can join a hospital room.

    Room name = "{slug}-call-{uuid}" (matches livekit_agent._resolve_call_target),
    so every call lands in a fresh room. The token embeds a RoomConfiguration
    with RoomAgentDispatch(agent_name="arya"); LiveKit Cloud uses that to
    dispatch the worker into the room on creation (Cloud Agents do NOT
    auto-dispatch — explicit dispatch via the token is required).

    Rate-limited per IP and the slug must resolve to a known active tenant
    (or "default") so random slugs can't dispatch billable agent rooms.
    """
    from fastapi import HTTPException

    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(status_code=503, detail="LiveKit not configured")

    client_ip = (request.client.host if request.client else "") or "unknown"
    if not _token_rate_ok(client_ip):
        logger.warning("token_rate_limited", ip=client_ip, slug=slug)
        raise HTTPException(status_code=429, detail="Too many requests")

    slug = (slug or "default").strip().lower()
    if slug != "default":
        try:
            from src.tenancy import registry
            if not await registry.get_tenant(slug):
                raise HTTPException(status_code=404, detail="Unknown tenant")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("token_tenant_lookup_failed", slug=slug, error=str(e))
            raise HTTPException(status_code=503, detail="Tenant lookup failed")

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
                    agents=[RoomAgentDispatch(agent_name=settings.LIVEKIT_DISPATCH_NAME)],
                )
            )
            .to_jwt()
        )
        return {"token": token, "room": room_name, "url": settings.LIVEKIT_URL}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")


# ── Outbound calls ───────────────────────────────────────────────────────────

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
    import os
    _admin_prefix = os.environ.get("ADMIN_PREFIX", "/admin")
    app.include_router(admin_router, prefix=_admin_prefix, tags=["admin"])
    # Also mount at /admin so the Next.js frontend (which proxies /admin/api/* ->
    # backend /admin/*) reaches the same handlers. Real auth protects these now.
    if _admin_prefix != "/admin":
        app.include_router(admin_router, prefix="/admin", tags=["admin"])
    logger.info("dashboard_mounted", path=_admin_prefix)
except Exception as e:
    logger.error("dashboard_mount_failed", error=str(e))

try:
    from dashboard.routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
except Exception:
    pass
  # ── Dashboard additions (analytics, QA, live monitoring, users/RBAC) ──
try:
    from additions.wiring import register_additions
    register_additions(app)
    logger.info("additions_mounted")
except Exception as e:
    logger.error("additions_mount_failed", error=str(e))
  
