"""
STT Provider abstraction layer.

Supported providers:
  - Sarvam AI Saarika (PRIMARY — best for Kerala Malayalam)
  - Deepgram Nova-2 (FALLBACK)
  - Azure Cognitive Speech (FALLBACK-2)

Sarvam Saarika is specifically designed for Indian languages including
Malayalam with regional dialect handling — ideal for this use case.
It also costs significantly less than Western STT providers for Indic languages.

All providers return a normalized STTResult.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


class STTProvider(str, Enum):
    SARVAM = "sarvam"
    DEEPGRAM = "deepgram"
    AZURE = "azure"


@dataclass
class STTResult:
    """Normalized output from any STT provider."""

    transcript: str
    confidence: float            # 0.0 – 1.0
    is_partial: bool             # True if mid-utterance
    provider: str
    latency_ms: int
    language_detected: Optional[str] = None  # "ml", "en", "mixed"
    raw: Optional[dict] = None   # provider-specific payload for debugging


@dataclass
class STTError:
    provider: str
    error_code: str
    message: str
    recoverable: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Sarvam AI Saarika STT
# Best for Kerala Malayalam — trained on Indic languages
# API: https://api.sarvam.ai/speech-to-text
# ─────────────────────────────────────────────────────────────────────────────


class SarvamSTT:
    """
    Sarvam AI Saarika ASR.

    Sarvam has two STT models:
      - saarika:v1        — Malayalam + regional dialects
      - saarika:v2        — improved accuracy (if available)

    Endpoint: POST https://api.sarvam.ai/speech-to-text
    Audio format: 16kHz mono WAV or PCM (mulaw for telephony)
    """

    BASE_URL = "https://api.sarvam.ai"
    STT_ENDPOINT = "/speech-to-text"
    STT_TRANSLATE_ENDPOINT = "/speech-to-text-translate"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"api-subscription-key": api_key},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

    async def transcribe_chunk(
        self,
        audio_bytes: bytes,
        language: str = "ml-IN",
        model: str = "saarika:v2",
        with_timestamps: bool = False,
    ) -> STTResult | STTError:
        """
        Transcribe a complete audio chunk.
        For streaming, chunk size should be ~ 1-3 seconds of audio.

        Audio requirements:
          - Format: WAV (PCM 16-bit)
          - Sample rate: 16000 Hz
          - Channels: mono
          - Max size: 25MB per request
        """
        t_start = time.monotonic()

        try:
            files = {
                "file": ("audio.wav", audio_bytes, "audio/wav"),
            }
            data = {
                "model": model,
                "language_code": language,
                "with_timestamps": str(with_timestamps).lower(),
            }

            response = await self._client.post(
                self.STT_ENDPOINT,
                files=files,
                data=data,
            )
            response.raise_for_status()
            payload = response.json()

            latency_ms = int((time.monotonic() - t_start) * 1000)

            transcript = payload.get("transcript", "")
            # Sarvam doesn't always return a confidence score directly
            # Use a heuristic based on transcript length and quality signals
            confidence = _estimate_confidence(transcript, payload)

            return STTResult(
                transcript=transcript,
                confidence=confidence,
                is_partial=False,
                provider=STTProvider.SARVAM,
                latency_ms=latency_ms,
                language_detected=payload.get("language_code", language),
                raw=payload,
            )

        except httpx.HTTPStatusError as e:
            return STTError(
                provider=STTProvider.SARVAM,
                error_code=f"HTTP_{e.response.status_code}",
                message=str(e),
                recoverable=e.response.status_code in (429, 503, 504),
            )
        except httpx.TimeoutException:
            return STTError(
                provider=STTProvider.SARVAM,
                error_code="TIMEOUT",
                message="Sarvam STT request timed out",
                recoverable=True,
            )
        except Exception as e:
            return STTError(
                provider=STTProvider.SARVAM,
                error_code="UNKNOWN",
                message=str(e),
                recoverable=True,
            )

    async def close(self) -> None:
        await self._client.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# Deepgram STT (fallback — supports streaming)
# ─────────────────────────────────────────────────────────────────────────────


class DeepgramSTT:
    """
    Deepgram Nova-2 for streaming Malayalam transcription.
    Used as fallback when Sarvam is unavailable.
    """

    WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, language: str = "ml"):
        self.api_key = api_key
        self.language = language

    async def transcribe_chunk(
        self, audio_bytes: bytes, language: Optional[str] = None
    ) -> STTResult | STTError:
        """Single-chunk transcription via Deepgram REST API."""
        lang = language or self.language
        t_start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.deepgram.com/v1/listen?model=nova-2&language={lang}"
                    "&smart_format=true&punctuate=true",
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "audio/wav",
                    },
                    content=audio_bytes,
                )
                response.raise_for_status()
                payload = response.json()

            latency_ms = int((time.monotonic() - t_start) * 1000)

            # Extract from Deepgram response structure
            channels = payload.get("results", {}).get("channels", [])
            if not channels:
                return STTError(
                    provider=STTProvider.DEEPGRAM,
                    error_code="EMPTY_RESPONSE",
                    message="No channels in Deepgram response",
                )

            alt = channels[0].get("alternatives", [{}])[0]
            transcript = alt.get("transcript", "")
            confidence = alt.get("confidence", 0.5)

            return STTResult(
                transcript=transcript,
                confidence=confidence,
                is_partial=False,
                provider=STTProvider.DEEPGRAM,
                latency_ms=latency_ms,
                raw=payload,
            )

        except Exception as e:
            return STTError(
                provider=STTProvider.DEEPGRAM,
                error_code="ERROR",
                message=str(e),
                recoverable=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Composite STT — tries Sarvam first, falls back to Deepgram
# ─────────────────────────────────────────────────────────────────────────────


class CompositeSTT:
    """
    Primary: Sarvam AI Saarika (best Malayalam)
    Fallback: Deepgram Nova-2

    Falls back automatically on error or low confidence.
    Logs provider used for observability.
    """

    def __init__(self):
        self._sarvam: Optional[SarvamSTT] = None
        self._deepgram: Optional[DeepgramSTT] = None

        if settings.SARVAM_API_KEY:
            self._sarvam = SarvamSTT(api_key=settings.SARVAM_API_KEY)

        if settings.DEEPGRAM_API_KEY:
            self._deepgram = DeepgramSTT(
                api_key=settings.DEEPGRAM_API_KEY,
                language=settings.DEEPGRAM_LANGUAGE,
            )

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "ml-IN",
    ) -> STTResult:
        """
        Transcribe audio with automatic fallback.
        Returns best available result.
        """
        result = None

        # 1. Try Sarvam first
        if self._sarvam:
            result = await self._sarvam.transcribe_chunk(audio_bytes, language=language)
            if isinstance(result, STTResult) and result.confidence >= settings.STT_CONFIDENCE_THRESHOLD:
                result.provider = STTProvider.SARVAM
                return result
            elif isinstance(result, STTError):
                logger.warning("sarvam_stt_error", error=result.error_code)
                result = None

        # 2. Fallback to Deepgram
        if self._deepgram and (result is None or settings.ENABLE_FALLBACK_STT):
            dg_result = await self._deepgram.transcribe_chunk(audio_bytes)
            if isinstance(dg_result, STTResult):
                dg_result.stt_fallback_used = True if result is not None else False
                return dg_result

        # 3. Return what we have even if low confidence
        if isinstance(result, STTResult):
            return result

        # 4. Complete failure — return empty transcript
        return STTResult(
            transcript="",
            confidence=0.0,
            is_partial=False,
            provider="none",
            latency_ms=0,
        )

    async def close(self) -> None:
        if self._sarvam:
            await self._sarvam.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _estimate_confidence(transcript: str, payload: dict) -> float:
    """
    Estimate confidence from Sarvam response.
    Sarvam v2 may return a confidence field; if not, use heuristics.
    """
    # Check if Sarvam provides confidence directly
    if "confidence" in payload:
        return float(payload["confidence"])

    # Heuristic: empty transcript = 0, longer = higher
    if not transcript:
        return 0.0
    if len(transcript) < 3:
        return 0.3
    if len(transcript) < 10:
        return 0.55
    return 0.75  # reasonable default for non-empty transcript
