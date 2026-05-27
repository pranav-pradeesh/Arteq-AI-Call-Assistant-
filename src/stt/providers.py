"""
STT Provider — Sarvam AI Saarika v2.5.

Primary (and only) provider: Sarvam saarika:v2.5
Best-in-class for Kerala Malayalam with regional dialect handling.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class STTResult:
    transcript: str
    confidence: float
    is_partial: bool
    provider: str
    latency_ms: int
    language_detected: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class STTError:
    provider: str
    error_code: str
    message: str
    recoverable: bool = True


class SarvamSTT:
    """Sarvam AI Saarika ASR — saarika:v2.5."""

    BASE_URL = "https://api.sarvam.ai"
    STT_ENDPOINT = "/speech-to-text"

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
        language: str = "unknown",
        model: str = "saarika:v2.5",
    ) -> STTResult | STTError:
        """
        language: 'unknown' enables Sarvam's auto-detect (best for Manglish);
        otherwise pass 'ml-IN', 'en-IN', 'hi-IN', etc.
        """
        t_start = time.monotonic()
        try:
            response = await self._client.post(
                self.STT_ENDPOINT,
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data={"model": model, "language_code": language},
            )
            if response.status_code >= 400:
                logger.error(
                    "sarvam_stt_http_error",
                    status=response.status_code,
                    body=response.text[:500],
                    model=model,
                    language=language,
                )
                return STTError(
                    provider="sarvam",
                    error_code=f"HTTP_{response.status_code}",
                    message=response.text[:200],
                    recoverable=response.status_code in (429, 503, 504),
                )
            payload = response.json()
            latency_ms = int((time.monotonic() - t_start) * 1000)
            transcript = payload.get("transcript", "")
            confidence = _estimate_confidence(transcript, payload)
            logger.info(
                "sarvam_stt_ok",
                transcript=transcript[:120],
                lang_detected=payload.get("language_code"),
                latency_ms=latency_ms,
            )
            return STTResult(
                transcript=transcript,
                confidence=confidence,
                is_partial=False,
                provider="sarvam",
                latency_ms=latency_ms,
                language_detected=payload.get("language_code", language),
                raw=payload,
            )
        except httpx.HTTPStatusError as e:
            return STTError(
                provider="sarvam",
                error_code=f"HTTP_{e.response.status_code}",
                message=str(e),
                recoverable=e.response.status_code in (429, 503, 504),
            )
        except httpx.TimeoutException:
            return STTError(provider="sarvam", error_code="TIMEOUT",
                            message="Sarvam STT timed out", recoverable=True)
        except Exception as e:
            return STTError(provider="sarvam", error_code="UNKNOWN",
                            message=str(e), recoverable=True)

    async def close(self) -> None:
        await self._client.aclose()


class CompositeSTT:
    """Wraps SarvamSTT with a clean transcribe() interface."""

    def __init__(self):
        self._sarvam: Optional[SarvamSTT] = None
        if settings.SARVAM_API_KEY:
            self._sarvam = SarvamSTT(api_key=settings.SARVAM_API_KEY)

    async def transcribe(self, audio_bytes: bytes, language: str = "ml-IN") -> STTResult:
        if self._sarvam:
            result = await self._sarvam.transcribe_chunk(
                audio_bytes, language=language, model=settings.SARVAM_STT_MODEL
            )
            if isinstance(result, STTResult):
                return result
            logger.warning("sarvam_stt_error", error=result.error_code)

        return STTResult(transcript="", confidence=0.0, is_partial=False,
                         provider="none", latency_ms=0)

    async def close(self) -> None:
        if self._sarvam:
            await self._sarvam.close()


def _estimate_confidence(transcript: str, payload: dict) -> float:
    if "confidence" in payload:
        return float(payload["confidence"])
    if not transcript:
        return 0.0
    if len(transcript) < 3:
        return 0.3
    if len(transcript) < 10:
        return 0.55
    return 0.75
