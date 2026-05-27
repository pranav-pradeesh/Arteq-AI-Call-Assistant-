"""
TTS Engine.

Supported providers:
  - Sarvam AI Bulbul (PRIMARY — best Malayalam, lowest cost)
  - Azure Neural TTS (FALLBACK — ml-IN-SobhanaNeural)
  - Google Cloud TTS (FALLBACK-2)

Sarvam Bulbul is purpose-built for Indic languages with natural
Kerala Malayalam prosody — far better than Azure/Google for this use case.

All responses are cached by text hash to avoid re-synthesis of
identical phrases (greetings, clarifications, etc.)
"""

from __future__ import annotations

import hashlib
import io
import time
from typing import Optional

import httpx

from src.cache.redis_client import cache_get, cache_set
from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

# TTS audio cache TTL (cache synthesized audio for common phrases)
TTS_CACHE_TTL = 86400  # 24 hours


class TTSResult:
    def __init__(self, audio_bytes: bytes, format: str, latency_ms: int, provider: str):
        self.audio_bytes = audio_bytes
        self.format = format           # "wav", "mp3", "mulaw"
        self.latency_ms = latency_ms
        self.provider = provider


# ─────────────────────────────────────────────────────────────────────────────
# Sarvam AI Bulbul TTS
# Best Malayalam neural voice for Kerala
# API: https://api.sarvam.ai/text-to-speech
# ─────────────────────────────────────────────────────────────────────────────


class SarvamTTS:
    """
    Sarvam Bulbul TTS.

    Available voices (Malayalam):
      - anushka       - female, clear, formal
      - arvind        - male
      - amol          - male, warm
      - neel          - male
      - maitreyi      - female, natural
      - pavithra      - female
      - (more at api.sarvam.ai)

    Output format: WAV 22050Hz or 8000Hz (telephony)
    """

    BASE_URL = "https://api.sarvam.ai"
    TTS_ENDPOINT = "/text-to-speech"

    # Best voices for hospital receptionist persona
    FEMALE_VOICE = "anushka"   # Clear, professional, warm
    MALE_VOICE = "arvind"

    def __init__(self, api_key: str, voice: Optional[str] = None):
        self.api_key = api_key
        self.voice = voice or self.FEMALE_VOICE
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"api-subscription-key": api_key},
            timeout=httpx.Timeout(15.0, connect=3.0),
        )

    async def synthesize(
        self,
        text: str,
        language: str = "ml-IN",
        target_sample_rate: int = 8000,   # telephony standard
    ) -> TTSResult | None:
        """
        Synthesize Malayalam text to audio.
        Returns PCM audio suitable for telephony streaming.
        """
        t_start = time.monotonic()

        try:
            payload = {
                "inputs": [text],
                "target_language_code": language,
                "speaker": self.voice,
                "pitch": 0,
                "pace": 1.0,            # natural speaking rate
                "loudness": 1.5,
                "speech_sample_rate": target_sample_rate,
                "enable_preprocessing": True,
                "model": "bulbul:v1",
            }

            response = await self._client.post(
                self.TTS_ENDPOINT,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

            latency_ms = int((time.monotonic() - t_start) * 1000)

            # Sarvam returns base64-encoded audio in "audios" array
            import base64
            audios = result.get("audios", [])
            if not audios:
                return None

            audio_bytes = base64.b64decode(audios[0])

            return TTSResult(
                audio_bytes=audio_bytes,
                format="wav",
                latency_ms=latency_ms,
                provider="sarvam",
            )

        except Exception as e:
            logger.error("sarvam_tts_error", error=str(e))
            return None

    async def close(self) -> None:
        await self._client.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# Azure Neural TTS (fallback)
# ─────────────────────────────────────────────────────────────────────────────


class AzureTTS:
    """Azure Cognitive Services TTS — ml-IN-SobhanaNeural."""

    def __init__(self, api_key: str, region: str, voice: str):
        self.api_key = api_key
        self.region = region
        self.voice = voice
        self._token_url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
        self._synth_url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    async def _get_token(self) -> str:
        """Get Azure auth token (cached 9 minutes)."""
        if time.time() < self._token_expires:
            return self._token

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                self._token_url,
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
            )
            r.raise_for_status()
            self._token = r.text
            self._token_expires = time.time() + 540  # 9 min
            return self._token

    async def synthesize(self, text: str, language: str = "ml-IN") -> TTSResult | None:
        t_start = time.monotonic()
        try:
            token = await self._get_token()
            ssml = (
                f'<speak version="1.0" xml:lang="{language}">'
                f'<voice name="{self.voice}">{text}</voice></speak>'
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    self._synth_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": "riff-8khz-16bit-mono-pcm",
                    },
                    content=ssml.encode("utf-8"),
                )
                r.raise_for_status()
                audio = r.content

            return TTSResult(
                audio_bytes=audio,
                format="wav",
                latency_ms=int((time.monotonic() - t_start) * 1000),
                provider="azure",
            )
        except Exception as e:
            logger.error("azure_tts_error", error=str(e))
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Composite TTS with caching
# ─────────────────────────────────────────────────────────────────────────────


class CompositeTTS:
    """
    TTS with automatic fallback and audio caching.

    Common phrases (greetings, clarifications) are cached in Redis
    so they are synthesized only once and served from cache after that.
    Cache key = hash(text + voice).
    """

    def __init__(self):
        self._sarvam: Optional[SarvamTTS] = None
        self._azure: Optional[AzureTTS] = None

        if settings.SARVAM_API_KEY:
            voice = settings.SARVAM_TTS_VOICE or SarvamTTS.FEMALE_VOICE
            self._sarvam = SarvamTTS(api_key=settings.SARVAM_API_KEY, voice=voice)

        if settings.AZURE_SPEECH_KEY:
            self._azure = AzureTTS(
                api_key=settings.AZURE_SPEECH_KEY,
                region=settings.AZURE_SPEECH_REGION,
                voice=settings.AZURE_TTS_VOICE,
            )

    async def synthesize(self, text: str, language: str = "ml-IN") -> Optional[bytes]:
        """
        Synthesize text to audio bytes.
        Returns raw audio suitable for streaming to caller.
        Checks cache first; stores successful synthesis in cache.
        """
        if not text or not text.strip():
            return None

        cache_key = f"tts:{_text_hash(text)}:{language}"

        # Check audio cache
        cached = await cache_get(cache_key)
        if cached:
            import base64
            return base64.b64decode(cached)

        # Synthesize
        result = await self._synthesize_with_fallback(text, language)
        if result is None:
            logger.error("tts_all_providers_failed", text_preview=text[:50])
            return None

        # Cache the result
        import base64
        await cache_set(cache_key, base64.b64encode(result.audio_bytes).decode(), ttl=TTS_CACHE_TTL)

        logger.info("tts_synthesized", provider=result.provider, latency_ms=result.latency_ms)
        return result.audio_bytes

    async def _synthesize_with_fallback(
        self, text: str, language: str
    ) -> Optional[TTSResult]:
        # 1. Try Sarvam
        if self._sarvam:
            result = await self._sarvam.synthesize(text, language=language)
            if result:
                return result

        # 2. Try Azure
        if self._azure:
            result = await self._azure.synthesize(text, language=language)
            if result:
                return result

        return None

    async def close(self) -> None:
        if self._sarvam:
            await self._sarvam.close()


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
