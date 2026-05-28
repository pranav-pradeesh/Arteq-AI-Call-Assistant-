"""
STT Providers — Google Cloud Speech-to-Text v1.

Multilingual: STT_LANGUAGES env var lists BCP-47 codes in priority order.
Primary language = first code. Google STT v1 supports up to 3 alternatives.
Language detected is passed to Gemini brain and TTS for matching response voice.
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


class GoogleSTT:
    """
    Google Cloud Speech-to-Text v1 REST API.

    Multilingual: reads STT_LANGUAGES (comma-separated BCP-47 codes).
    First code = primary language. Next up to 3 = alternativeLanguageCodes.
    Google returns the detected language in each result for TTS routing.
    """

    STT_URL = "https://speech.googleapis.com/v1/speech:recognize"

    async def transcribe_chunk(
        self,
        audio_bytes: bytes,
        language: str = "ml-IN",
    ) -> STTResult | STTError:
        import base64 as _b64
        t_start = time.monotonic()
        try:
            # Parse STT_LANGUAGES: "ml-IN,en-IN,hi-IN,..." → primary + alternatives
            # Google STT v1 supports up to 3 alternativeLanguageCodes.
            lang_list = [l.strip() for l in settings.STT_LANGUAGES.split(",") if l.strip()]
            if not lang_list:
                lang_list = ["ml-IN", "en-IN", "hi-IN"]
            # "unknown" → use configured primary language
            lang_code = lang_list[0] if language == "unknown" else language
            alt_langs = [l for l in lang_list[1:4] if l != lang_code]
            payload = {
                "config": {
                    "encoding": "LINEAR16",
                    "sampleRateHertz": 16000,
                    "languageCode": lang_code,
                    "alternativeLanguageCodes": alt_langs,
                    "model": "default",
                    "useEnhanced": False,
                    "enableAutomaticPunctuation": False,
                },
                "audio": {"content": _b64.b64encode(audio_bytes).decode()},
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                response = await client.post(
                    self.STT_URL,
                    params={"key": settings.GOOGLE_API_KEY},
                    json=payload,
                )
            latency_ms = int((time.monotonic() - t_start) * 1000)
            if response.status_code >= 400:
                logger.error("google_stt_http_error", status=response.status_code,
                             body=response.text[:300])
                return STTError(
                    provider="google",
                    error_code=f"HTTP_{response.status_code}",
                    message=response.text[:200],
                    recoverable=response.status_code in (429, 503, 504),
                )
            data = response.json()
            results = data.get("results", [])
            if not results:
                return STTResult(transcript="", confidence=0.0, is_partial=False,
                                 provider="google", latency_ms=latency_ms)
            alt = results[0].get("alternatives", [{}])[0]
            transcript = alt.get("transcript", "")
            confidence = float(alt.get("confidence", 0.75))
            detected_lang = results[0].get("languageCode", lang_code)
            logger.info("google_stt_ok", transcript=transcript[:120],
                        lang=detected_lang, latency_ms=latency_ms)
            return STTResult(
                transcript=transcript,
                confidence=confidence,
                is_partial=False,
                provider="google",
                latency_ms=latency_ms,
                language_detected=detected_lang,
            )
        except httpx.TimeoutException:
            return STTError(provider="google", error_code="TIMEOUT",
                            message="Google STT timed out", recoverable=True)
        except Exception as e:
            return STTError(provider="google", error_code="UNKNOWN",
                            message=str(e), recoverable=True)


class CompositeSTT:
    """Google STT wrapper with empty-result fallback."""

    def __init__(self):
        self._google: Optional[GoogleSTT] = None
        if settings.GOOGLE_API_KEY:
            self._google = GoogleSTT()
        else:
            logger.warning("google_stt_no_api_key_set")

    async def transcribe(self, audio_bytes: bytes, language: str = "ml-IN") -> STTResult:
        if self._google:
            result = await self._google.transcribe_chunk(audio_bytes, language=language)
            if isinstance(result, STTResult):
                return result
            logger.warning("google_stt_error", error=result.error_code,
                           msg=result.message[:100])

        return STTResult(transcript="", confidence=0.0, is_partial=False,
                         provider="none", latency_ms=0)

    async def close(self) -> None:
        pass
