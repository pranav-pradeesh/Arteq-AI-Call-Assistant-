"""
TTS Engine — Sarvam Bulbul v3.

In-memory audio cache (keyed by text hash + language) avoids
re-synthesising identical phrases within the process lifetime.
"""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Optional

from src.cache.store import tts_cache, TTS_CACHE_TTL
from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


class TTSResult:
    def __init__(self, audio_bytes: bytes, latency_ms: int):
        self.audio_bytes = audio_bytes
        self.latency_ms = latency_ms


class CompositeTTS:
    """TTS with in-memory audio cache. Provider: Sarvam Bulbul v3."""

    def __init__(self):
        from src.tts.sarvam_tts import SarvamTTS
        self._sarvam: Optional[SarvamTTS] = None
        if settings.SARVAM_API_KEY:
            self._sarvam = SarvamTTS()
        else:
            logger.warning("sarvam_tts_no_api_key")

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
        logger.info("tts_synthesized", latency_ms=result.latency_ms, provider="sarvam")
        return result.audio_bytes

    async def _synthesize_raw(self, text: str, language: str) -> Optional[TTSResult]:
        if self._sarvam:
            t_start = time.monotonic()
            pcm = await self._sarvam.synthesize(text, language=language)
            if pcm is not None:
                return TTSResult(audio_bytes=pcm,
                                 latency_ms=int((time.monotonic() - t_start) * 1000))
            logger.warning("sarvam_tts_failed")
        return None

    async def close(self) -> None:
        pass


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


async def warm_tts_cache(phrases: list[tuple[str, str]]) -> int:
    """Pre-synthesize fixed (text, language) phrases into the process TTS cache.

    Run once at startup so the greeting and common prompts play instantly on
    the first call instead of waiting on a cold-start Sarvam TTS request.
    Returns the number of phrases successfully warmed.
    """
    tts = CompositeTTS()
    warmed = 0
    for text, language in phrases:
        try:
            audio = await tts.synthesize(text, language=language)
            if audio:
                warmed += 1
        except Exception as exc:
            logger.warning("tts_warm_phrase_failed", error=str(exc), text=text[:40])
    return warmed
