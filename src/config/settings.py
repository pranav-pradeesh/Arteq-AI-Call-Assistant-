"""Settings — reads directly from the project .env file."""
from __future__ import annotations
import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000
    HOSPITAL_ID: str = "00000000-0000-0000-0000-000000000001"

    # Telephony
    TELEPHONY_MODE: str = "stream"
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WS_URL: str = "ws://localhost:8000"

    # LiveKit — voice agent + SIP trunking
    LIVEKIT_URL: str = ""          # wss://your-project.livekit.cloud
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    # Explicit-dispatch agent name. Worker registers under this name and the
    # token endpoint dispatches to it. MUST match between worker + token.
    # Override locally (e.g. "arya-local") to isolate a dev worker from the
    # deployed "arya" worker on the same LiveKit project — otherwise Cloud
    # load-balances calls across both and a stale prod worker can answer.
    LIVEKIT_DISPATCH_NAME: str = "arya"
    LIVEKIT_SIP_HOST: str = ""     # e.g. "xx.sip.livekit.cloud" — from LiveKit dashboard
    LIVEKIT_SIP_OUTBOUND_TRUNK_ID: str = ""  # set after running POST /admin/sip/setup

    @model_validator(mode="after")
    def _auto_detect_render_url(self) -> "Settings":
        """On Render, RENDER_EXTERNAL_URL is injected automatically.
        Use it if the user hasn't set PUBLIC_BASE_URL explicitly."""
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        if render_url and self.PUBLIC_BASE_URL in (
            "http://localhost:8000", "http://localhost:8000/"
        ):
            self.PUBLIC_BASE_URL = render_url.rstrip("/")
            self.PUBLIC_WS_URL = (
                render_url.rstrip("/")
                .replace("https://", "wss://")
                .replace("http://", "ws://")
            )
        return self

    # Provider selection
    TTS_PROVIDER: str = "sarvam"
    STT_PROVIDER: str = "sarvam"
    STT_CONFIDENCE_THRESHOLD: float = 0.55

    # AI Brain
    DEFAULT_LANGUAGE: str = "ml-IN"

    # Sarvam AI — STT (Saarika v2.5 / Saaras v3) + TTS (Bulbul v3)
    # Supported STT language codes (BCP-47 xx-IN format):
    #   hi-IN, bn-IN, ta-IN, te-IN, kn-IN, ml-IN, mr-IN, gu-IN, od-IN, pa-IN, en-IN
    #   (saaras:v3 also adds: as-IN, ur-IN, sa-IN, ne-IN, mai-IN, kok-IN, ks-IN,
    #    doi-IN, mni-IN, brx-IN, sat-IN, sd-IN — all 22 constitutional languages)
    # Use "unknown" for automatic language detection (recommended for multi-lingual clinics).
    # Note: Odia uses "od-IN" (Sarvam non-standard), NOT the ISO "or-IN".
    SARVAM_API_KEY: str = ""

    # Google Cloud — Gemini AI brain
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-2.0-flash"

    # WhatsApp — Meta Cloud API (patient notifications; no SMS).
    # Business-initiated messages use pre-approved "Utility" templates. Set up
    # the templates in Meta Business Manager and put their names below.
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_PHONE_NUMBER_ID: str = ""        # from Meta → WhatsApp → API Setup
    WHATSAPP_ACCESS_TOKEN: str = ""           # permanent system-user token
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_TEMPLATE_LANG: str = "en"        # language code of approved templates
    # Approved template names (override only if you named yours differently).
    WHATSAPP_TPL_CONFIRMATION: str = "appointment_confirmation"
    WHATSAPP_TPL_TOKEN_ACTIVE: str = "token_active"
    WHATSAPP_TPL_REMINDER: str = "appointment_reminder"
    WHATSAPP_TPL_CANCELLATION: str = "appointment_cancellation"
    WHATSAPP_TPL_DOCTOR_AVAIL: str = "doctor_availability"
    WHATSAPP_TPL_CALLBACK: str = "callback_confirmation"
    WHATSAPP_TPL_LOCATION: str = "hospital_location"
    WHATSAPP_TPL_LAB: str = "lab_schedule"

    # Database
    DATABASE_URL: str = ""
    # SSL mode for the Postgres pool: "auto" | "require" | "disable".
    # "auto" requires SSL for remote hosts (Supabase/cloud) and disables it for
    # local hosts (localhost/127.0.0.1/docker service names) so local dev works.
    DB_SSL: str = "auto"
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "tts-cache"

    # Cache / Session
    REDIS_URL: str = ""
    SESSION_TTL_S: int = 300
    CACHE_MAX_SIZE: int = 512

    # Intent thresholds
    INTENT_CONFIDENCE_THRESHOLD: float = 0.50
    MAX_CLARIFICATION_ATTEMPTS: int = 2

    # Persona
    AGENT_NAME: str = "Arya"
    AGENT_LANGUAGE: str = "ml-IN"

    # Outbound / transfer
    INTERNAL_API_KEY: str = ""
    REMINDERS_ENABLED: bool = True
    REMINDER_INTERVAL_SECONDS: int = 900

    # Advance confirmation calls (~1 week before appointment)
    CONFIRMATIONS_ENABLED: bool = True
    CONFIRMATION_LOOP_INTERVAL_SECONDS: int = 3600   # check every hour
    CONFIRMATION_DAYS_MIN: int = 6                   # start calling 6 days before
    CONFIRMATION_DAYS_MAX: int = 8                   # stop calling 8 days before (window centred on 7 days)

    # Callbacks
    CALLBACK_LOOP_INTERVAL_SECONDS: int = 300
    CALLBACKS_ENABLED: bool = True

    # Staff alerts — SMS sent to duty manager on key events
    STAFF_ALERT_PHONE: str = ""          # duty manager mobile; leave blank to disable
    STAFF_ALERT_ON_BOOKING: bool = True
    STAFF_ALERT_ON_EMERGENCY: bool = True
    STAFF_ALERT_ON_CANCEL: bool = True

    # Follow-up calls (3 days after appointment)
    FOLLOWUPS_ENABLED: bool = True
    FOLLOWUP_LOOP_INTERVAL_SECONDS: int = 3600
    FOLLOWUP_DAYS_AFTER: int = 3         # call patient this many days after appointment

    # Resume campaigns stranded by a restart (recipients left 'pending')
    CAMPAIGN_RESUME_ENABLED: bool = True
    CAMPAIGN_RESUME_INTERVAL_SECONDS: int = 600

    # Vobiz — SIP trunk provider (sole telephony carrier; no SMS — SIP-only)
    VOBIZ_API_KEY: str = ""
    VOBIZ_API_SECRET: str = ""
    VOBIZ_PHONE_NUMBER: str = ""                  # E.164 e.g. +918047XXXXXX
    VOBIZ_SIP_CIDRS: str = ""                     # comma-separated; leave blank for default
    # Outbound callee number format for Vobiz's dial plan (Vobiz 404s on raw E.164;
    # inbound caller-IDs are national 0-prefixed). national | cc | local | e164.
    VOBIZ_DIAL_FORMAT: str = "national"
    # SIP credentials LiveKit uses to authenticate OUTBOUND calls to Vobiz. Must
    # match a Vobiz Credentials-List entry on the outbound trunk. Falls back to
    # VOBIZ_API_KEY/SECRET if unset (legacy behaviour).
    VOBIZ_SIP_USERNAME: str = ""
    VOBIZ_SIP_PASSWORD: str = ""
    LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID: str = "" # set after POST /admin/sip/vobiz/setup

    # Vobiz call recording (disabled by default — check storage pricing in Vobiz console)
    VOBIZ_RECORD_CALLS: bool = False
    VOBIZ_RECORDING_FORMAT: str = "mp3"           # mp3 | wav
    VOBIZ_RECORDING_CHANNELS: str = "mono"        # mono | stereo

    # Doctor availability scheduler
    DOCTOR_AVAIL_ENABLED: bool = True
    DOCTOR_AVAIL_INTERVAL_SECONDS: int = 600      # poll every 10 min

    # Outbound reminder queue consumer (trial tier — 24h + 2h reminder calls).
    # Drains outbound_call_queue rows whose scheduled_at has arrived, within the
    # calling window, honouring the per-hospital reminder toggles.
    OUTBOUND_QUEUE_ENABLED: bool = True
    OUTBOUND_QUEUE_INTERVAL_SECONDS: int = 300    # poll every 5 min

    # Patient recognition
    PATIENT_RECOGNITION_ENABLED: bool = True

    # After-hours
    AFTER_HOURS_CALLBACK_ENABLED: bool = True

    # Post-call SMS summary (opt-in — costs per SMS)
    POST_CALL_SMS_ENABLED: bool = False

    # DTMF fallback
    DTMF_ENABLED: bool = True

    # CORS — comma-separated allowed origins. "*" allows any (dev only).
    # In production set to your dashboard/app origins e.g.
    # "https://arteq.example.com,https://admin.example.com".
    CORS_ORIGINS: str = "*"

    # LiveKit token endpoint abuse guard — per-IP tokens allowed per window.
    TOKEN_RATE_LIMIT: int = 12
    TOKEN_RATE_WINDOW_SECONDS: int = 60

    # Dashboard auth
    DASHBOARD_ADMIN_PASSWORD: str = "admin"
    DASHBOARD_JWT_SECRET: str = "change-me-in-production"
    DASHBOARD_JWT_EXPIRE_MINUTES: int = 720
    # Email the startup superadmin upsert uses (password = DASHBOARD_ADMIN_PASSWORD).
    SUPERADMIN_EMAIL: str = "admin@arteqai.com"

    @model_validator(mode="after")
    def _reject_weak_secrets_in_production(self) -> "Settings":
        if self.ENV == "production":
            _WEAK = {"admin", "change-me-in-production", "change_me_in_production", ""}
            errors = []
            if self.DASHBOARD_JWT_SECRET in _WEAK:
                errors.append(
                    "DASHBOARD_JWT_SECRET is not set or is a default value. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.DASHBOARD_ADMIN_PASSWORD in _WEAK or len(self.DASHBOARD_ADMIN_PASSWORD) < 12:
                errors.append(
                    "DASHBOARD_ADMIN_PASSWORD must be at least 12 characters and not a default value."
                )
            if errors:
                import sys
                msg = (
                    "\n\n[ARTEQ STARTUP ERROR] Missing required secrets for production:\n"
                    + "\n".join(f"  • {e}" for e in errors)
                    + "\n\nSet these in Render Dashboard → Environment → Secret Files.\n"
                )
                print(msg, file=sys.stderr, flush=True)
                raise ValueError(msg)
        return self

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


try:
    settings = Settings()
except Exception as _settings_exc:
    import sys
    print(f"\n[ARTEQ FATAL] Settings validation failed: {_settings_exc}\n", file=sys.stderr, flush=True)
    raise
