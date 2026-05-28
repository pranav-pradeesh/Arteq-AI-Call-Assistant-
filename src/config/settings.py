"""Settings — reads directly from the project .env file."""
from __future__ import annotations
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
    EXOTEL_CALLER_ID: str = ""
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WS_URL: str = "ws://localhost:8000"

    # Sarvam AI
    SARVAM_API_KEY: str = ""
    SARVAM_STT_MODEL: str = "saarika:v2.5"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_VOICE_ML: str = "kavitha"
    SARVAM_TTS_VOICE_EN: str = "kavitha"

    # STT
    STT_PROVIDER: str = "sarvam"
    STT_CONFIDENCE_THRESHOLD: float = 0.55

    # Groq LLM
    GROQ_API_KEY: str = ""
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"
    GROQ_MODEL_SMART: str = "llama-3.3-70b-versatile"
    GROQ_MAX_TOKENS: int = 100
    GROQ_TIMEOUT_S: int = 8

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

    # Google Cloud (optional STT/TTS provider)
    GOOGLE_API_KEY: str = ""

    # Dashboard auth
    DASHBOARD_ADMIN_PASSWORD: str = "admin"
    DASHBOARD_JWT_SECRET: str = "change-me-in-production"
    DASHBOARD_JWT_EXPIRE_MINUTES: int = 720

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


settings = Settings()
