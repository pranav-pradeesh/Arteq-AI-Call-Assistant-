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
    EXOTEL_SID: str = ""
    EXOTEL_API_KEY: str = ""
    EXOTEL_API_TOKEN: str = ""
    EXOTEL_CALLER_ID: str = ""
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WS_URL: str = "ws://localhost:8000"

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
    EXOTEL_SUBDOMAIN: str = "api.exotel.in"
    REMINDERS_ENABLED: bool = True
    REMINDER_INTERVAL_SECONDS: int = 900

    # Advance confirmation calls (1–2 weeks before appointment)
    CONFIRMATIONS_ENABLED: bool = True
    CONFIRMATION_LOOP_INTERVAL_SECONDS: int = 3600   # check every hour
    CONFIRMATION_DAYS_MIN: int = 5                   # start calling 5 days before
    CONFIRMATION_DAYS_MAX: int = 14                  # stop calling 14 days before

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

    # Patient recognition
    PATIENT_RECOGNITION_ENABLED: bool = True

    # After-hours
    AFTER_HOURS_CALLBACK_ENABLED: bool = True

    # Post-call SMS summary (opt-in — costs per SMS)
    POST_CALL_SMS_ENABLED: bool = False

    # DTMF fallback
    DTMF_ENABLED: bool = True

    # Dashboard auth
    DASHBOARD_ADMIN_PASSWORD: str = "admin"
    DASHBOARD_JWT_SECRET: str = "change-me-in-production"
    DASHBOARD_JWT_EXPIRE_MINUTES: int = 720

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


settings = Settings()
