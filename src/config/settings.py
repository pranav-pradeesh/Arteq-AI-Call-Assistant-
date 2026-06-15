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

    # Plivo — SIP trunk + phone number provisioning
    PLIVO_AUTH_ID: str = ""
    PLIVO_AUTH_TOKEN: str = ""
    PLIVO_PHONE_NUMBER: str = ""   # E.164 e.g. +918047XXXXXX

    # Exotel — Indian cloud telephony (alternative to Plivo)
    EXOTEL_API_KEY: str = ""          # Account SID (shown in Exotel dashboard)
    EXOTEL_API_TOKEN: str = ""        # API token
    EXOTEL_PHONE_NUMBER: str = ""     # Virtual Number / ExoPhone, E.164 +91XXXXXXXXXX
    EXOTEL_SUBDOMAIN: str = "api.exotel.com"  # or api.in.exotel.com for India region
    # Embed in webhook URL so only Exotel (who was given the URL) can trigger it.
    # Leave blank to skip token check (not recommended in production).
    EXOTEL_WEBHOOK_TOKEN: str = ""
    LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID: str = ""  # set after POST /admin/sip/exotel/setup

    # Exotel transport for the conversation audio path:
    #   "sip"       → forward the call to LiveKit over SIP (get_inbound_exoml)
    #   "websocket" → Exotel Voicebot/AgentStream applet streams raw audio over a
    #                 WebSocket which we bridge into a LiveKit room (get_voicebot_exoml).
    # The WebSocket path runs no SIP trunk — Exotel speaks raw/slin 16-bit 8 kHz
    # mono PCM (little-endian, base64) directly to /ws/exotel/stream/{token}/{slug}.
    EXOTEL_TRANSPORT: str = "sip"
    # Outgoing audio chunk size sent back to Exotel. Must be a multiple of 320
    # bytes; Exotel requires min 3200 and max 100000 bytes per media frame.
    # 3200 bytes = 100 ms of 8 kHz/16-bit/mono PCM.
    EXOTEL_STREAM_CHUNK_BYTES: int = 3200
    # Exotel App/flow id whose Voicebot applet streams to our WS, used to place
    # outbound WebSocket-streamed calls via the Exotel Connect API. Leave blank
    # if outbound-over-WebSocket is not used (SIP outbound still works).
    EXOTEL_VOICEBOT_APP_ID: str = ""

    # WhatsApp (Plivo WhatsApp API). When enabled, patient notifications go via
    # WhatsApp and fall back to SMS if a send fails or WhatsApp is unconfigured.
    WHATSAPP_ENABLED: bool = True
    PLIVO_WHATSAPP_NUMBER: str = ""   # WABA sender, E.164 e.g. +918047XXXXXX
    WHATSAPP_FALLBACK_TO_SMS: bool = True

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

    # Sarvam AI — STT (Saarika v2) + TTS (Bulbul v3)
    SARVAM_API_KEY: str = ""

    # Groq AI — LLaMA brain
    GROQ_API_KEY: str = ""

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
