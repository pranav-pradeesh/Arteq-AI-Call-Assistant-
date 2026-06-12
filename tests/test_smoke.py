"""
Smoke tests — verify imports and pure-function logic without a live DB or API.
Run: pytest tests/test_smoke.py
"""
import asyncio


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
    from src.db.queries import HospitalContext
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


def test_token_rate_limiter():
    """Per-IP token guard allows up to the limit, then blocks within the window."""
    from src import main
    main._token_hits.clear()
    limit = main.settings.TOKEN_RATE_LIMIT
    ip = "1.2.3.4"
    for _ in range(limit):
        assert main._token_rate_ok(ip) is True
    # Next request in the same window is rejected.
    assert main._token_rate_ok(ip) is False
    # A different IP is unaffected.
    assert main._token_rate_ok("5.6.7.8") is True
    main._token_hits.clear()


def test_features_normalize_over_tier():
    """normalize() fills every known key; clinic leaner than hospital."""
    from src.tenancy import features as feat
    hosp = feat.normalize({}, "hospital")
    clinic = feat.normalize({}, "clinic")
    assert set(hosp.keys()) == set(feat.FEATURES.keys())
    assert set(clinic.keys()) == set(feat.FEATURES.keys())
    # Explicit override wins over tier default.
    overridden = feat.normalize({"campaigns": False}, "hospital")
    assert overridden["campaigns"] is False


def test_plivo_signature_v1_and_v2():
    """Both Plivo signature schemes verify correctly and reject tampering."""
    import base64
    import hashlib
    import hmac
    from src.api.security import verify_plivo_signature_v1, verify_plivo_signature_v2

    token = "test-auth-token"
    url = "https://example.com/api/v1/call/inbound/demo"

    params = {"From": "+919999999999", "To": "+918888888888"}
    sorted_str = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    sig_v1 = base64.b64encode(
        hmac.new(token.encode(), (url + sorted_str).encode(), hashlib.sha1).digest()
    ).decode()
    assert verify_plivo_signature_v1(token, url, params, sig_v1) is True
    assert verify_plivo_signature_v1(token, url, params, "forged") is False
    assert verify_plivo_signature_v1("wrong-token", url, params, sig_v1) is False

    nonce = "12345"
    sig_v2 = base64.b64encode(
        hmac.new(token.encode(), (url + nonce).encode(), hashlib.sha256).digest()
    ).decode()
    assert verify_plivo_signature_v2(token, url, nonce, sig_v2) is True
    assert verify_plivo_signature_v2(token, url, "other-nonce", sig_v2) is False


def test_admin_token_carries_super_admin_role():
    """Legacy single-password tokens carry role=super_admin so RBAC-aware
    routes (additions/*) authorise them coherently."""
    from jose import jwt
    from src.config.settings import settings
    from dashboard.routes.admin_api import _create_token, _is_super

    payload = jwt.decode(
        _create_token(), settings.DASHBOARD_JWT_SECRET, algorithms=["HS256"]
    )
    assert payload["sub"] == "admin"
    assert payload["role"] == "super_admin"
    assert _is_super(payload) is True
    assert _is_super({"sub": "viewer@x.com", "role": "viewer"}) is False


def test_decode_token_accepts_both_shapes_rejects_garbage():
    from datetime import datetime, timedelta, timezone
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from jose import jwt
    from src.config.settings import settings
    from dashboard.routes.admin_api import _decode_token

    def creds(token):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    rbac = jwt.encode(
        {"sub": "staff@hosp.com", "role": "tenant_admin", "exp": exp},
        settings.DASHBOARD_JWT_SECRET, algorithm="HS256",
    )
    assert _decode_token(creds(rbac))["role"] == "tenant_admin"

    # Valid signature but neither legacy sub nor a known role -> 401
    rogue = jwt.encode(
        {"sub": "nobody", "exp": exp}, settings.DASHBOARD_JWT_SECRET, algorithm="HS256"
    )
    for bad in (rogue, "not-a-jwt"):
        try:
            _decode_token(creds(bad))
            assert False, "expected HTTPException"
        except HTTPException as e:
            assert e.status_code == 401


def test_mark_intent_dedupes():
    from src.telephony.livekit_tools import _mark_intent

    class Ctx:
        userdata = {}

    ctx = Ctx()
    _mark_intent(ctx, "book_appointment")
    _mark_intent(ctx, "book_appointment")
    _mark_intent(ctx, "emergency")
    assert ctx.userdata["intents"] == ["book_appointment", "emergency"]
