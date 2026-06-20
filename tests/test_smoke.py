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
    morning = build_greeting_text("Kairali Hospital", 9)
    assert "Kairali Hospital" in morning
    assert "Arya" not in morning  # agent identifies by hospital, not a name

    evening = build_greeting_text("Kairali Hospital", 19)
    assert "Kairali Hospital" in evening


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


def test_suffix_logic_dative():
    from utils.suffix_logic import choose_suffix, format_doctor
    assert choose_suffix("Lakshmi") == "-യ്ക്ക്"     # ends in 'i' → vowel
    assert choose_suffix("Ranjith") == "-ന്"           # ends in 'h' → consonant
    assert choose_suffix("Anita") == "-യ്ക്ക്"        # ends in 'a' → vowel
    assert choose_suffix("Suresh Kumar") == "-ന്"      # ends in 'r' → consonant
    assert format_doctor("Lakshmi").startswith("Dr. Lakshmi")
    assert "-യ്ക്ക്" in format_doctor("Lakshmi")


def test_suffix_logic_possessive():
    from utils.suffix_logic import choose_possessive, format_doctor_possessive
    assert choose_possessive("Lakshmi") == "-യുടെ"
    assert choose_possessive("Menon") == "-ന്റെ"
    assert "-യുടെ" in format_doctor_possessive("Anitha")
    assert "-ന്റെ" in format_doctor_possessive("Ranjith Menon")


def test_detect_tts_lang_scripts():
    from src.tts_normalize import detect_tts_lang
    ml_text = "ഞാൻ doctor appointment വേണം"
    assert detect_tts_lang(ml_text, "ml-IN") == "ml-IN"

    ta_text = "நான் டாக்டரை பார்க்க வேண்டும்"
    assert detect_tts_lang(ta_text, "ml-IN") == "ta-IN"

    hi_text = "मुझे डॉक्टर से मिलना है"
    assert detect_tts_lang(hi_text, "ml-IN") == "hi-IN"


def test_detect_tts_lang_manglish_uses_fallback():
    from src.tts_normalize import detect_tts_lang
    manglish = "Doctor appointment veenam paranjalo"
    assert detect_tts_lang(manglish, "ml-IN") == "ml-IN"
    assert detect_tts_lang(manglish, "en-IN") == "en-IN"


def test_detect_tts_lang_real_english_gets_english_phonetics():
    # Genuine English from a Malayalam (ml-IN) tenant must route to en-IN so it
    # is spoken in English, not with Malayalam phonetics in the Malayalam voice.
    from src.tts_normalize import detect_tts_lang
    english = "Sorry, I can only help with matters related to the hospital. How can I help you?"
    assert detect_tts_lang(english, "ml-IN") == "en-IN"
    # A real English reply that happens to mention a hospital term stays English.
    assert detect_tts_lang("Your appointment with the doctor is confirmed.", "ml-IN") == "en-IN"
    # Manglish still keeps Malayalam phonetics for the same tenant.
    assert detect_tts_lang("Doctor ne kaanan appointment venam", "ml-IN") == "ml-IN"


def test_normalize_for_tts_exact_match():
    from src.tts_normalize import normalize_for_tts
    result = normalize_for_tts("ഡോക്ടർ ഉണ്ട്")
    assert "Doctor" in result
    assert "ഡോക്ടർ" not in result

    result = normalize_for_tts("appointment ബുക്ക് ചെയ്യണം")
    assert "appointment" in result


def test_normalize_for_tts_stem_absorption():
    from src.tts_normalize import normalize_for_tts
    # "അപ്പോയിന്റ്മെന്റിന്" = "appointment" + dative suffix
    result = normalize_for_tts("അപ്പോയിന്റ്മെന്റിന് time എന്ത്?")
    assert "appointment" in result


def test_greeting_language_matrix():
    from src.ai.groq_brain import build_greeting_text, _HOW_CAN_I_HELP
    hosp = "Test Hospital"
    hour = 10

    # Every supported language names the hospital and never a personal agent name.
    for lang in list(_HOW_CAN_I_HELP) + ["od-IN", "mr-IN", "manglish"]:
        text = build_greeting_text(hosp, hour, lang=lang)
        assert hosp in text, f"hosp name missing for lang={lang}"
        assert "Arya" not in text, f"agent name leaked for lang={lang}"

    # Malayalam fallback: uses time-of-day opener, hospital only
    ml = build_greeting_text(hosp, 9, lang="ml-IN")
    assert "Good morning" in ml or "Good" in ml
    assert hosp in ml


def test_voice_for_lang_maps_to_real_v3_speakers():
    from src.tts_normalize import voice_for_lang, LANG_VOICE, BULBUL_V3_SPEAKERS
    # Known languages get a dedicated native-fit voice; unknown falls back.
    assert voice_for_lang("ml-IN", "priya") == "kavitha"
    assert voice_for_lang("ta-IN", "priya") == "shruti"
    assert voice_for_lang("zz-ZZ", "priya") == "priya"
    # English intentionally keeps the call's default speaker (no voice switch).
    assert voice_for_lang("en-IN", "priya") == "priya"
    # Every mapped voice must be a real Bulbul v3 speaker (else Sarvam 400s).
    for code, voice in LANG_VOICE.items():
        assert voice in BULBUL_V3_SPEAKERS, f"{voice} ({code}) not a v3 speaker"


def test_mark_intent_dedupes():
    from src.telephony.livekit_tools import _mark_intent

    class Ctx:
        userdata = {}

    ctx = Ctx()
    _mark_intent(ctx, "book_appointment")
    _mark_intent(ctx, "book_appointment")
    _mark_intent(ctx, "emergency")
    assert ctx.userdata["intents"] == ["book_appointment", "emergency"]
