"""
Tenant registry CRUD — operates on the CONTROL database `tenants` table.

The control DB is the existing settings.DATABASE_URL (reached via
src.db.queries.get_pool()). Each row maps a hospital/clinic slug to its own
Supabase connection string (db_url), tier, persona, and per-tenant feature
flags. Operational data lives in each tenant's own DB.

Feature flags are stored normalized over tier defaults so every known key is
present; see src/tenancy/features.py.
"""
from __future__ import annotations

import json
from typing import Optional

from src.db.queries import get_control_pool
from src.observability.logger import get_logger
from src.tenancy import features as feat

logger = get_logger(__name__)

# Columns the admin form can set (slug/id/created_at handled separately).
_EDITABLE = (
    "name", "name_ml", "tier", "db_url", "plivo_number",
    "agent_name", "agent_language", "address", "phone",
    "contact_person", "contact_phone", "notes", "active",
)


def _row_to_tenant(row) -> dict:
    t = dict(row)
    raw = t.get("features")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    t["features"] = feat.normalize(raw, t.get("tier", "hospital"))
    return t


async def get_tenant(slug: str) -> Optional[dict]:
    """Resolve a tenant by slug. Returns None if unknown.

    Cached (TTL) because it sits on the pre-greeting critical path of every
    inbound call; writes below invalidate the slug.
    """
    from src.cache.store import tenant_cache, TENANT_CACHE_TTL
    hit = tenant_cache.get(slug)
    if hit is not None:
        return hit or None      # cached miss stored as {}
    pool = await get_control_pool()
    row = await pool.fetchrow("SELECT * FROM tenants WHERE slug = $1", slug)
    tenant = _row_to_tenant(row) if row else None
    tenant_cache.set(slug, tenant or {}, ttl=TENANT_CACHE_TTL)
    return tenant


def _invalidate_tenant(slug: str) -> None:
    from src.cache.store import tenant_cache
    tenant_cache.delete(slug)


async def get_tenant_by_plivo(number: str) -> Optional[dict]:
    """Resolve a tenant by its inbound Plivo DID. Returns None if unknown."""
    pool = await get_control_pool()
    row = await pool.fetchrow(
        "SELECT * FROM tenants WHERE plivo_number = $1 AND active = TRUE", number
    )
    return _row_to_tenant(row) if row else None


async def list_tenants(include_inactive: bool = True) -> list[dict]:
    pool = await get_control_pool()
    if include_inactive:
        rows = await pool.fetch("SELECT * FROM tenants ORDER BY created_at DESC")
    else:
        rows = await pool.fetch(
            "SELECT * FROM tenants WHERE active = TRUE ORDER BY created_at DESC"
        )
    return [_row_to_tenant(r) for r in rows]


async def create_tenant(
    *,
    slug: str,
    name: str,
    tier: str = "hospital",
    db_url: str = "",
    features: Optional[dict] = None,
    name_ml: str = "",
    plivo_number: str = "",
    agent_name: str = "Arya",
    agent_language: str = "ml-IN",
    address: str = "",
    phone: str = "",
    contact_person: str = "",
    contact_phone: str = "",
    notes: str = "",
    active: bool = True,
) -> dict:
    """Insert a registry row. Features default to the tier matrix, then any
    explicit overrides are merged on top. Slug must be unique."""
    merged = feat.normalize(features, tier)
    pool = await get_control_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO tenants (
            slug, name, name_ml, tier, db_url, features, plivo_number,
            agent_name, agent_language, address, phone, contact_person,
            contact_phone, notes, active
        ) VALUES (
            $1, $2, $3, $4, $5, $6::jsonb, $7,
            $8, $9, $10, $11, $12,
            $13, $14, $15
        )
        RETURNING *
        """,
        slug, name, name_ml, tier, db_url, json.dumps(merged), plivo_number,
        agent_name, agent_language, address, phone, contact_person,
        contact_phone, notes, active,
    )
    logger.info("tenant_created", slug=slug, tier=tier)
    _invalidate_tenant(slug)
    return _row_to_tenant(row)


async def update_tenant(slug: str, fields: dict) -> Optional[dict]:
    """Patch editable columns on a tenant. Unknown keys ignored. If `tier`
    changes, feature defaults are NOT auto-reset (use set_features for that)."""
    sets = {k: v for k, v in fields.items() if k in _EDITABLE}
    if not sets:
        return await get_tenant(slug)
    cols = list(sets.keys())
    assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    values = [sets[c] for c in cols]
    pool = await get_control_pool()
    row = await pool.fetchrow(
        f"UPDATE tenants SET {assignments} WHERE slug = $1 RETURNING *",
        slug, *values,
    )
    _invalidate_tenant(slug)
    return _row_to_tenant(row) if row else None


async def set_features(slug: str, features: dict) -> Optional[dict]:
    """Replace a tenant's feature map (normalized over its tier defaults)."""
    current = await get_tenant(slug)
    if not current:
        return None
    merged = feat.normalize(features, current.get("tier", "hospital"))
    pool = await get_control_pool()
    row = await pool.fetchrow(
        "UPDATE tenants SET features = $2::jsonb WHERE slug = $1 RETURNING *",
        slug, json.dumps(merged),
    )
    _invalidate_tenant(slug)
    return _row_to_tenant(row) if row else None


async def deactivate_tenant(slug: str) -> bool:
    pool = await get_control_pool()
    result = await pool.execute(
        "UPDATE tenants SET active = FALSE WHERE slug = $1", slug
    )
    _invalidate_tenant(slug)
    return result.endswith("1")
