"""
STT Providers — Sarvam Saarika v2.

Auto-detects language across all major Indian languages + Manglish.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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


class CompositeSTT:
    """STT dispatcher. Provider: Sarvam Saarika v2 (language auto-detect)."""

    def __init__(self):
        from src.stt.sarvam_stt import SarvamSTT
        self._sarvam: Optional[SarvamSTT] = None
        if settings.SARVAM_API_KEY:
            self._sarvam = SarvamSTT()
        else:
            logger.warning("sarvam_stt_no_api_key")

    async def transcribe(self, audio_bytes: bytes, language: str = "ml-IN") -> STTResult:
        if self._sarvam:
            transcript, lang, confidence = await self._sarvam.transcribe(audio_bytes)
            return STTResult(
                transcript=transcript,
                confidence=confidence,
                is_partial=False,
                provider="sarvam",
                latency_ms=0,
                language_detected=lang,
            )
        return STTResult(transcript="", confidence=0.0, is_partial=False,
                         provider="none", latency_ms=0)

    async def close(self) -> None:
        pass
