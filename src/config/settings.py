"""
Application settings loaded from environment / .env file.
All values are type-checked and validated at startup.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = "change_this_in_production"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000

    # ── Database ─────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://arteq:arteq@localhost:5432/arteq_hospital"
    SYNC_DATABASE_URL: str = "postgresql://arteq:arteq@localhost:5432/arteq_hospital"

    # ── Redis ────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 300

    # ── STT — Sarvam AI (PRIMARY) ─────────────────────────────
    SARVAM_API_KEY: str = ""
    SARVAM_STT_MODEL: str = "saarika:v2"
    SARVAM_STT_LANGUAGE: str = "ml-IN"
    SARVAM_TTS_VOICE: str = "anushka"   # female, clear, professional

    # ── STT — Deepgram (FALLBACK) ─────────────────────────────
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_MODEL: str = "nova-2"
    DEEPGRAM_LANGUAGE: str = "ml"
    DEEPGRAM_FALLBACK_LANGUAGE: str = "en"

    # ── TTS — Azure (FALLBACK) ────────────────────────────────
    AZURE_SPEECH_KEY: str = ""
    AZURE_SPEECH_REGION: str = "eastus"
    AZURE_TTS_VOICE: str = "ml-IN-SobhanaNeural"
    AZURE_TTS_FALLBACK_VOICE: str = "ml-IN-MidhunNeural"

    # ── LLM — Anthropic (response composition only) ───────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_MAX_TOKENS: int = 150
    ENABLE_LLM_RESPONSE: bool = True

    # ── STT Settings ─────────────────────────────────────────
    STT_CONFIDENCE_THRESHOLD: float = 0.65
    ENABLE_FALLBACK_STT: bool = True

    # ── Intent / Clarification ───────────────────────────────
    INTENT_CONFIDENCE_THRESHOLD: float = 0.55
    MAX_CLARIFICATION_ATTEMPTS: int = 2

    # ── Dashboard ────────────────────────────────────────────
    DASHBOARD_JWT_SECRET: str = "change_this_in_production"
    DASHBOARD_JWT_EXPIRE_MINUTES: int = 480

    # ── Telephony (Exotel — primary for India) ────────────────
    EXOTEL_SID: str = ""
    EXOTEL_API_KEY: str = ""
    EXOTEL_API_TOKEN: str = ""
    EXOTEL_VIRTUAL_NUMBER: str = ""

    # Twilio (alternative)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""

    @field_validator("APP_ENV")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"APP_ENV must be one of {allowed}")
        return v

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def primary_stt_available(self) -> bool:
        return bool(self.SARVAM_API_KEY)

    @property
    def primary_tts_available(self) -> bool:
        return bool(self.SARVAM_API_KEY)


settings = Settings()
