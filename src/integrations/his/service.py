"""
HIS service — factory and per-hospital adapter cache.

Usage in tools:
    adapter = await get_his_adapter(hospital_id)
    if adapter:
        slots = await adapter.get_available_slots(doctor_id, date)
    else:
        slots = await local_db_get_slots(...)   # fallback
"""
from __future__ import annotations

from typing import Optional

from src.integrations.his.base import HISAdapter
from src.observability.logger import get_logger

logger = get_logger(__name__)

# Simple in-process cache: hospital_id → adapter (or None if not configured)
_adapter_cache: dict[str, Optional[HISAdapter]] = {}


async def get_his_adapter(hospital_id: str) -> Optional[HISAdapter]:
    """
    Return the configured HIS adapter for this hospital, or None.

    Result is cached in-process. Call invalidate_his_cache(hospital_id)
    after updating his_config via the admin API.
    """
    if hospital_id in _adapter_cache:
        return _adapter_cache[hospital_id]

    try:
        from src.db.queries import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT his_config FROM hospitals WHERE id=$1", hospital_id
            )
        if not row or not row["his_config"]:
            _adapter_cache[hospital_id] = None
            return None

        import json
        cfg = row["his_config"] if isinstance(row["his_config"], dict) else json.loads(row["his_config"])

        if not cfg.get("enabled", False):
            _adapter_cache[hospital_id] = None
            return None

        adapter = _build_adapter(cfg)
        _adapter_cache[hospital_id] = adapter
        if adapter:
            logger.info("his_adapter_loaded", hospital_id=hospital_id, type=cfg.get("type"))
        return adapter

    except Exception as exc:
        logger.warning("his_adapter_load_failed", hospital_id=hospital_id, error=str(exc))
        _adapter_cache[hospital_id] = None
        return None


def invalidate_his_cache(hospital_id: str) -> None:
    """Call this whenever his_config is updated for a hospital."""
    _adapter_cache.pop(hospital_id, None)


def _build_adapter(cfg: dict) -> Optional[HISAdapter]:
    adapter_type = cfg.get("type", "generic_rest")
    try:
        if adapter_type == "fhir":
            from src.integrations.his.fhir import FHIRAdapter
            return FHIRAdapter(cfg)
        # Default: generic_rest
        from src.integrations.his.generic_rest import GenericRestAdapter
        return GenericRestAdapter(cfg)
    except Exception as exc:
        logger.error("his_adapter_build_failed", type=adapter_type, error=str(exc))
        return None


async def his_status(hospital_id: str) -> dict:
    """Return HIS connectivity status for the admin dashboard."""
    try:
        from src.db.queries import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT his_config FROM hospitals WHERE id=$1", hospital_id
            )
        if not row or not row["his_config"]:
            return {"configured": False, "enabled": False, "reachable": None}

        import json
        cfg = row["his_config"] if isinstance(row["his_config"], dict) else json.loads(row["his_config"])
        enabled = cfg.get("enabled", False)
        if not enabled:
            return {"configured": True, "enabled": False, "reachable": None, "type": cfg.get("type")}

        adapter = _build_adapter(cfg)
        reachable = await adapter.ping() if adapter else False
        return {
            "configured": True,
            "enabled": True,
            "type": cfg.get("type", "generic_rest"),
            "base_url": cfg.get("base_url", ""),
            "reachable": reachable,
        }
    except Exception as exc:
        return {"configured": False, "enabled": False, "reachable": False, "error": str(exc)}
