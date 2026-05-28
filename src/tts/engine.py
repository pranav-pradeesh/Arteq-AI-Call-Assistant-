"""
TTS Engine — Sarvam AI Bulbul v3, voice: kavitha.

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

import httpx

from src.cache.store import tts_cache, TTS_CACHE_TTL
from src.config.settings import settings
from src.observability.logger import get_logger
from src.tts.google_tts import GoogleTTS

logger = get_logger(__name__)


def _wav_to_pcm_8k_mono(wav_bytes: bytes) -> bytes:
    """
    Normalize Sarvam's WAV output to raw PCM16 mono @ 8 kHz.

    Sarvam Bulbul v3 may return 22050 or 24000 Hz regardless of the
    sample_rate request param. Read the header for the *actual* rate
    and resample down — otherwise Exotel plays it ~3× too fast
    (the "demon voice" effect).
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
    except wave.Error as e:
        logger.error("tts_wav_parse_failed", error=str(e))
        # Best-effort fallback: strip 44-byte RIFF header
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
        logger.error("tts_audioop_failed", error=str(e), framerate=framerate, channels=channels)
        return wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
    return pcm


class TTSResult:
    def __init__(self, audio_bytes: bytes, latency_ms: int):
        self.audio_bytes = audio_bytes
        self.latency_ms = latency_ms


class SarvamTTS:
    """Sarvam Bulbul v3 TTS — bulbul:v3, voice: kavitha."""

    BASE_URL = "https://api.sarvam.ai"
    TTS_ENDPOINT = "/text-to-speech"

    def __init__(self, api_key: str, voice: str = "kavitha"):
        self.api_key = api_key
        self.voice = voice
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"api-subscription-key": api_key},
            timeout=httpx.Timeout(15.0, connect=3.0),
        )

    async def synthesize(
        self,
        text: str,
        language: str = "ml-IN",
        sample_rate: int = 8000,
    ) -> Optional[TTSResult]:
        t_start = time.monotonic()
        model = settings.SARVAM_TTS_MODEL

        # Per Sarvam docs:
        #   - field name is "text" (singular) and "sample_rate" (not "speech_sample_rate")
        #   - bulbul:v3 rejects pitch and loudness
        payload = {
            "text": text,
            "target_language_code": language,
            "speaker": self.voice,
            "model": model,
            "pace": 1.0,
            "sample_rate": sample_rate,
            "enable_preprocessing": True,
        }
        if not model.startswith("bulbul:v3"):
            payload["pitch"] = 0
            payload["loudness"] = 1.5

        try:
            resp = await self._client.post(self.TTS_ENDPOINT, json=payload)
            if resp.status_code >= 400:
                logger.error(
                    "sarvam_tts_http_error",
                    status=resp.status_code,
                    body=resp.text[:500],
                    model=model,
                    voice=self.voice,
                )
                return None
            data = resp.json()
            audios = data.get("audios", [])
            if not audios:
                logger.error("sarvam_tts_empty", response=str(data)[:200])
                return None
            # Response is base64-encoded WAV. Parse header for real rate
            # and resample to 8 kHz mono — Sarvam v3 ignores sample_rate
            # request and returns 22050/24000 Hz, which Exotel plays too fast.
            wav_bytes = base64.b64decode(audios[0])
            pcm_bytes = _wav_to_pcm_8k_mono(wav_bytes)
            return TTSResult(
                audio_bytes=pcm_bytes,
                latency_ms=int((time.monotonic() - t_start) * 1000),
            )
        except Exception as e:
            logger.error("sarvam_tts_error", error=str(e), model=model, voice=self.voice)
            return None

    async def close(self) -> None:
        await self._client.aclose()


class CompositeTTS:
    """
    TTS with in-memory audio cache.
    Cache key = hash(text + language).

    Provider selection (via settings.TTS_PROVIDER):
      - "google"  → Google Cloud TTS Neural2, falls back to Sarvam on failure
      - "sarvam"  → Sarvam Bulbul v3 only (default)
    """

    def __init__(self):
        self._google: Optional[GoogleTTS] = None
        if settings.TTS_PROVIDER == "google" and settings.GOOGLE_CLOUD_TTS_KEY:
            self._google = GoogleTTS()

        self._sarvam: Optional[SarvamTTS] = None
        if settings.SARVAM_API_KEY:
            self._sarvam = SarvamTTS(
                api_key=settings.SARVAM_API_KEY,
                voice=settings.SARVAM_TTS_VOICE_ML,
            )

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

        tts_cache.set(cache_key, base64.b64encode(result.audio_bytes).decode(), ttl=TTS_CACHE_TTL)
        logger.info("tts_synthesized", latency_ms=result.latency_ms)
        return result.audio_bytes

    async def _synthesize_raw(self, text: str, language: str) -> Optional[TTSResult]:
        # Try Google TTS first if configured
        if self._google:
            t_start = time.monotonic()
            pcm = await self._google.synthesize(text, language=language)
            if pcm is not None:
                return TTSResult(audio_bytes=pcm, latency_ms=int((time.monotonic() - t_start) * 1000))
            logger.warning("google_tts_fallback_to_sarvam")
        # Sarvam fallback
        if self._sarvam:
            return await self._sarvam.synthesize(text, language=language)
        return None

    async def close(self) -> None:
        # GoogleTTS is stateless (no persistent client), nothing to close
        if self._sarvam:
            await self._sarvam.close()


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
