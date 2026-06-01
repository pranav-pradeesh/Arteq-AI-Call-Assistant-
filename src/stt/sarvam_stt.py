"""
Sarvam Saarika v2.5 STT provider.

Handles Manglish (Malayalam in English script) natively alongside all
major Indian languages and English.

Input : raw PCM16 mono @ 16 kHz (already up-sampled by websocket_handler)
Output: (transcript, language_code, confidence)
"""
from __future__ import annotations

import asyncio
import io
import time
import wave

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

_SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

# Languages plausible on a Kerala hospital line. Sarvam's auto-detect
# occasionally mislabels short/noisy Malayalam clips as Punjabi (pa-IN),
# Odia (od-IN), Assamese, etc. Anything outside this set is treated as
# Malayalam so the brain and TTS don't reply in the wrong language.
_PLAUSIBLE_LANGS = {"ml-IN", "en-IN", "ta-IN", "kn-IN", "te-IN", "hi-IN"}
_DEFAULT_LANG = "ml-IN"


def _normalize_detected_lang(lang: str) -> str:
    """Keep South-Indian/Hindi/English detections; map the rest to Malayalam."""
    return lang if lang in _PLAUSIBLE_LANGS else _DEFAULT_LANG


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw PCM16 mono data in a standard WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)        # mono
        wf.setsampwidth(2)        # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class SarvamSTT:
    """
    Sarvam Saarika v2 speech-to-text.

    Requires SARVAM_API_KEY in environment / .env.
    Uses saarika:v2.5; language_code="unknown" triggers auto-detection.
    """

    def __init__(self) -> None:
        self._api_key: str = getattr(settings, "SARVAM_API_KEY", "")

    async def transcribe(self, audio_bytes: bytes) -> tuple[str, str, float]:
        """
        Transcribe PCM16 mono @ 16 kHz audio.

        Returns:
            (transcript, language_code, confidence)
            On error: ("", "ml-IN", 0.0)
        """
        t_start = time.monotonic()
        wav_bytes = _pcm_to_wav(audio_bytes, sample_rate=16000)

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0, connect=3.0)
                ) as client:
                    response = await client.post(
                        _SARVAM_STT_URL,
                        headers={"api-subscription-key": self._api_key},
                        files={
                            "file": ("audio.wav", wav_bytes, "audio/wav"),
                        },
                        data={
                            "model": "saarika:v2.5",
                            "language_code": "unknown",  # "unknown" = auto-detect
                        },
                    )

                latency_ms = int((time.monotonic() - t_start) * 1000)

                if response.status_code in (429, 503) and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue

                if response.status_code >= 400:
                    logger.error(
                        "sarvam_stt_error",
                        error=f"HTTP {response.status_code}",
                        status=response.status_code,
                        body=response.text[:300],
                    )
                    return ("", "ml-IN", 0.0)

                data = response.json()
                transcript: str = data.get("transcript", "")
                raw_lang: str = data.get("language_code", "ml-IN") or "ml-IN"
                language_code = _normalize_detected_lang(raw_lang)
                confidence: float = 0.9

                logger.info(
                    "sarvam_stt_ok",
                    transcript=transcript[:100],
                    lang=language_code,
                    raw_lang=raw_lang,
                    latency_ms=latency_ms,
                )
                return (transcript, language_code, confidence)

            except httpx.TimeoutException as exc:
                logger.error("sarvam_stt_error", error=f"Timeout: {exc}")
                return ("", "ml-IN", 0.0)
            except Exception as exc:
                logger.error("sarvam_stt_error", error=str(exc))
                return ("", "ml-IN", 0.0)

        return ("", "ml-IN", 0.0)
