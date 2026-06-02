"""
Smoke tests — verify imports and pure-function logic without a live DB or API.
Run: pytest tests/test_smoke.py
"""
import asyncio
import pytest


def test_settings_import():
    from src.config.settings import settings
    assert settings.AGENT_NAME == "Arya"
    assert settings.DEFAULT_LANGUAGE == "ml-IN"
    assert settings.HOSPITAL_ID == "00000000-0000-0000-0000-000000000001"


def test_build_greeting_text():
    from src.ai.groq_brain import build_greeting_text
    morning = build_greeting_text("Kairali Hospital", "Arya", 9)
    assert "Arya" in morning
    assert "Kairali Hospital" in morning

    evening = build_greeting_text("Kairali Hospital", "Arya", 19)
    assert "Arya" in evening


def test_parse_slot_valid():
    from src.telephony.livekit_tools import _parse_slot
    slot = _parse_slot("2025-12-25", "10:30")
    assert slot is not None
    assert slot.hour == 10
    assert slot.minute == 30
    assert slot.day == 25


def test_parse_slot_invalid():
    from src.telephony.livekit_tools import _parse_slot
    assert _parse_slot("not-a-date", "10:00") is None
    assert _parse_slot("2025-12-25", "bad-time") is None


def test_sms_service_skips_without_plivo():
    """SMSService silently skips (returns False) when Plivo is not configured."""
    from src.services.sms_service import SMSService
    result = asyncio.run(SMSService().send_custom("+919999999999", "smoke test"))
    assert result is False


def test_cache_store_crud():
    from src.cache.store import MemoryCache
    cache = MemoryCache(max_size=10)
    cache.set("k", "v", ttl=60)
    assert cache.get("k") == "v"
    cache.delete("k")
    assert cache.get("k") is None


def test_cache_store_ttl_expired():
    import time
    from src.cache.store import MemoryCache, _Entry
    cache = MemoryCache(max_size=10)
    cache._data["x"] = _Entry(value="expired", expires_at=time.time() - 1)
    assert cache.get("x") is None


def test_hospital_context_hours_for_day():
    """HospitalContext.hours_for_day returns (open, close) or None."""
    from src.db.queries import HospitalContext, DeptInfo
    ctx = HospitalContext(
        hospital_id="test",
        name="Test",
        name_ml="",
        address="",
        phone="",
        hours={"mon": ["08:00", "20:00"], "sun": None},
        departments=[],
        doctors=[],
        billing=[],
        faqs=[],
        emergency=[],
    )
    assert ctx.hours_for_day(1) == ("08:00", "20:00")  # Monday
    assert ctx.hours_for_day(0) is None   # Sunday (None value)
    assert ctx.hours_for_day(6) is None   # Saturday (not present)


def test_derive_slug():
    from dashboard.routes.admin_api import _derive_slug
    assert _derive_slug("Kairali Multi-Speciality Hospital") == "kairali-multi-speciality-hospital"
    assert _derive_slug("  Test  ") == "test"
    assert _derive_slug("ABC & XYZ") == "abc-xyz"
