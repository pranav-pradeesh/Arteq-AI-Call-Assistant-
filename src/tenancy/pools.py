"""
Multi-database connection routing.

- The CONTROL database (settings.DATABASE_URL) holds the tenant registry; reach
  it with the existing src.db.queries.get_pool().
- Each tenant has its OWN database, addressed by tenants.db_url. tenant_pool()
  lazily opens and caches an asyncpg pool per distinct db_url.
- provision_tenant_db() runs the full migration set against a fresh tenant DB so
  a newly onboarded hospital gets the complete schema.

If a tenant has no db_url (legacy / shared-DB tenants), callers fall back to the
control pool — the system keeps working in single-DB mode.
"""
from __future__ import annotations

import asyncio
import pathlib

import asyncpg

from src.db.queries import _resolve_ssl
from src.observability.logger import get_logger

logger = get_logger(__name__)

_pools: dict[str, asyncpg.Pool] = {}
_lock = asyncio.Lock()

_MIGRATIONS_DIR = pathlib.Path("migrations/versions")


def _norm(url: str) -> str:
    return (url or "").replace("postgresql+asyncpg://", "postgresql://").strip()


async def tenant_pool(db_url: str) -> asyncpg.Pool:
    """Return a cached pool for a tenant's own database."""
    url = _norm(db_url)
    if not url:
        raise ValueError("tenant_pool requires a non-empty db_url")
    existing = _pools.get(url)
    if existing is not None:
        return existing
    async with _lock:
        existing = _pools.get(url)
        if existing is None:
            existing = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=10,
                command_timeout=30,
                ssl=_resolve_ssl(url),
                timeout=20,
            )
            _pools[url] = existing
            logger.info("tenant_pool_opened", host=_host(url))
    return existing


async def provision_tenant_db(db_url: str) -> int:
    """Run every migration against a tenant DB. Returns count applied.

    Idempotent: migrations are IF NOT EXISTS / additive, so re-running is safe.
    Raises if the DB is unreachable so onboarding surfaces a clear error.
    """
    url = _norm(db_url)
    if not url:
        raise ValueError("provision_tenant_db requires a non-empty db_url")
    conn = await asyncpg.connect(url, ssl=_resolve_ssl(url), timeout=20)
    applied = 0
    try:
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            await conn.execute(sql_file.read_text(encoding="utf-8"))
            applied += 1
    finally:
        await conn.close()
    logger.info("tenant_db_provisioned", host=_host(url), migrations=applied)
    return applied


async def close_all() -> None:
    async with _lock:
        for url, pool in list(_pools.items()):
            try:
                await pool.close()
            except Exception:
                pass
            _pools.pop(url, None)


def _host(url: str) -> str:
    import urllib.parse
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
