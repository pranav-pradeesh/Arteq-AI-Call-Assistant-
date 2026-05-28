"""
Gemini TTS — uses Gemini 2.5 Flash TTS model via google-genai SDK.

Key advantages over Neural2:
- Natively multilingual: pass any language text, no voice map needed
- More expressive: natural pitch variation, emotion-aware
- Cheaper: $10/1M audio tokens vs $16/1M chars for Neural2

Audio pipeline: Gemini returns raw PCM16 at 24 kHz mono.
Exotel requires 8 kHz mono PCM16 → resample with audioop.
"""
from __future__ import annotations

import audioop
import time
from typing import Optional

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
_SOURCE_HZ = 24000
_TARGET_HZ = 8000
_MAX_CHARS = 5000

# Available Gemini TTS voices (prebuilt). Aoede = warm female, best for
# a hospital receptionist persona.
GEMINI_VOICES = {
    "Aoede",    # warm, female
    "Kore",     # firm, female
    "Zephyr",   # bright, female
    "Charon",   # informative, male
    "Fenrir",   # excitable, male
    "Puck",     # upbeat, male
}


class GeminiTTS:
    """Gemini 2.5 Flash TTS — natively multilingual, expressive audio."""

    def __init__(self):
        self._client = None
        self._types = None
        try:
            from google import genai
            from google.genai import types as genai_types
            if settings.GEMINI_API_KEY:
                self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
                self._types = genai_types
        except ImportError:
            logger.warning("gemini_tts_sdk_not_installed")

    def is_available(self) -> bool:
        return self._client is not None

    async def synthesize(self, text: str, voice: str = "Aoede") -> Optional[bytes]:
        if not self._client:
            return None

        text = text[:_MAX_CHARS]
        types = self._types
        voice = voice if voice in GEMINI_VOICES else "Aoede"

        t_start = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=_GEMINI_TTS_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice,
                            )
                        )
                    ),
                ),
            )
            latency_ms = int((time.monotonic() - t_start) * 1000)

            candidates = response.candidates
            if not candidates:
                logger.error("gemini_tts_no_candidates")
                return None

            candidate = candidates[0]
            if candidate.content is None:
                logger.error("gemini_tts_no_content",
                             finish_reason=str(candidate.finish_reason))
                return None

            parts = candidate.content.parts
            if not parts:
                logger.error("gemini_tts_no_parts")
                return None

            audio_part = parts[0]
            inline = getattr(audio_part, "inline_data", None)
            if inline is None or not inline.data:
                logger.error("gemini_tts_no_audio_data",
                             part_repr=str(audio_part)[:100])
                return None

            # Resample 24 kHz → 8 kHz for Exotel (PCM16 mono)
            pcm_8k, _ = audioop.ratecv(
                inline.data, 2, 1, _SOURCE_HZ, _TARGET_HZ, None
            )

            logger.info("gemini_tts_ok", voice=voice,
                        latency_ms=latency_ms, text_len=len(text),
                        pcm_bytes=len(pcm_8k))
            return pcm_8k

        except Exception as e:
            logger.error("gemini_tts_error", error=str(e),
                         text_preview=text[:60], voice=voice)
            return None
