"""
TTS Engine — Gemini TTS (default) with Google Neural2 as fallback.

Provider selection via TTS_PROVIDER env var:
  "gemini"  → Gemini 2.5 Flash TTS (natively multilingual, most expressive)
  "google"  → Google Cloud TTS Neural2 (language-specific voices)

Audio is cached in-memory by text hash to avoid re-synthesising
identical phrases (greeting, clarifications, etc.).
"""
from __future__ import annotations

import audioop
import base64
import hashlib
import io
import time
import wave
from typing import Optional

from src.cache.store import tts_cache, TTS_CACHE_TTL
from src.config.settings import settings
from src.observability.logger import get_logger
from src.tts.gemini_tts import GeminiTTS
from src.tts.google_tts import GoogleTTS

logger = get_logger(__name__)


def _wav_to_pcm_8k_mono(wav_bytes: bytes) -> bytes:
    """
    Convert WAV bytes to raw PCM16 mono @ 8 kHz.

    Handles WAVs at any sample rate (Google TTS returns 8 kHz directly,
    but reads the header to confirm and resample if needed).
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
    except wave.Error as e:
        logger.error("tts_wav_parse_failed", error=str(e))
        return wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes

    try:
        if channels == 2:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
        if sample_width != 2:
            pcm = audioop.lin2lin(pcm, sample_width, 2)
        if framerate != 8000:
            pcm, _ = audioop.ratecv(pcm, 2, 1, framerate, 8000, None)
            logger.info("tts_resampled", from_hz=framerate, to_hz=8000)
    except Exception as e:
        logger.error("tts_audioop_failed", error=str(e),
                     framerate=framerate, channels=channels)
        return wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
    return pcm


class TTSResult:
    def __init__(self, audio_bytes: bytes, latency_ms: int):
        self.audio_bytes = audio_bytes
        self.latency_ms = latency_ms


class CompositeTTS:
    """
    TTS with in-memory audio cache.
    Cache key = hash(text + language).

    Provider selection (TTS_PROVIDER env var):
      "gemini" → Gemini 2.5 Flash TTS  (default — multilingual, expressive)
      "google" → Google Cloud TTS Neural2
    """

    def __init__(self):
        self._gemini: Optional[GeminiTTS] = None
        if settings.TTS_PROVIDER == "gemini" and settings.GEMINI_API_KEY:
            self._gemini = GeminiTTS()

        self._google: Optional[GoogleTTS] = None
        if settings.TTS_PROVIDER == "google" and settings.GOOGLE_CLOUD_TTS_KEY:
            self._google = GoogleTTS()

        if not self._gemini and not self._google:
            logger.warning("tts_no_provider_configured",
                           provider=settings.TTS_PROVIDER)

    async def synthesize(self, text: str, language: str = "ml-IN") -> Optional[bytes]:
        if not text or not text.strip():
            return None

        cache_key = f"tts:{_text_hash(text)}:{language}"
        cached = tts_cache.get(cache_key)
        if cached is not None:
            return base64.b64decode(cached)

        result = await self._synthesize_raw(text, language)
        if result is None:
            logger.error("tts_failed", text_preview=text[:50])
            return None

        tts_cache.set(cache_key, base64.b64encode(result.audio_bytes).decode(),
                      ttl=TTS_CACHE_TTL)
        logger.info("tts_synthesized", latency_ms=result.latency_ms,
                    provider=settings.TTS_PROVIDER)
        return result.audio_bytes

    async def _synthesize_raw(self, text: str, language: str) -> Optional[TTSResult]:
        # Gemini TTS — natively multilingual, language param not needed
        if self._gemini:
            t_start = time.monotonic()
            pcm = await self._gemini.synthesize(text, voice=settings.GEMINI_TTS_VOICE)
            if pcm is not None:
                return TTSResult(audio_bytes=pcm,
                                 latency_ms=int((time.monotonic() - t_start) * 1000))
            logger.warning("gemini_tts_failed")

        # Google Neural2 fallback
        if self._google:
            t_start = time.monotonic()
            pcm = await self._google.synthesize(text, language=language)
            if pcm is not None:
                return TTSResult(audio_bytes=pcm,
                                 latency_ms=int((time.monotonic() - t_start) * 1000))
            logger.warning("google_tts_failed")

        return None

    async def close(self) -> None:
        pass  # Both providers are stateless (no persistent HTTP clients)


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
