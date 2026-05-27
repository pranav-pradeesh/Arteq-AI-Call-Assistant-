"""
TTS Engine — Sarvam AI Bulbul v3, voice: kavitha.

Audio is cached in-memory by text hash to avoid re-synthesising
identical phrases (greeting, clarifications, etc.).
"""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Optional

import httpx

from src.cache.store import tts_cache, TTS_CACHE_TTL
from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


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

        # bulbul:v2 expects {"inputs":[...]}, bulbul:v3 expects {"text": "..."}.
        if model.startswith("bulbul:v3") or model == "bulbul:v3":
            payload = {
                "text": text,
                "target_language_code": language,
                "speaker": self.voice,
                "pitch": 0,
                "pace": 1.0,
                "loudness": 1.5,
                "speech_sample_rate": sample_rate,
                "enable_preprocessing": True,
                "model": model,
            }
        else:
            payload = {
                "inputs": [text],
                "target_language_code": language,
                "speaker": self.voice,
                "pitch": 0,
                "pace": 1.0,
                "loudness": 1.5,
                "speech_sample_rate": sample_rate,
                "enable_preprocessing": True,
                "model": model,
            }

        try:
            resp = await self._client.post(self.TTS_ENDPOINT, json=payload)
            if resp.status_code >= 400:
                # Log Sarvam's actual error body so we can diagnose schema/voice errors
                body = resp.text[:500]
                logger.error(
                    "sarvam_tts_http_error",
                    status=resp.status_code,
                    body=body,
                    model=model,
                    voice=self.voice,
                )
                return None
            data = resp.json()
            audios = data.get("audios", [])
            if not audios:
                logger.error("sarvam_tts_empty", response=str(data)[:200])
                return None
            audio_bytes = base64.b64decode(audios[0])
            return TTSResult(
                audio_bytes=audio_bytes,
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
    """

    def __init__(self):
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
        if self._sarvam:
            return await self._sarvam.synthesize(text, language=language)
        return None

    async def close(self) -> None:
        if self._sarvam:
            await self._sarvam.close()


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
