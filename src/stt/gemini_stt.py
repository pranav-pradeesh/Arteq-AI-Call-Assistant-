"""
Gemini STT — transcribes audio using Gemini 2.5 Flash multimodal input.

Accepts raw PCM16 @ 8 kHz mono (Exotel format), upsamples to 16 kHz,
wraps in a WAV container, and asks Gemini to transcribe + detect language.
Returns (transcript, BCP-47 language code, confidence).
"""
from __future__ import annotations

import audioop
import io
import json
import time
import wave
from typing import Optional

from google import genai
from google.genai import types

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

_MODEL = "gemini-2.5-flash-preview-05-20"

_PROMPT = (
    "Transcribe the speech in this audio exactly as spoken. "
    "Detect the language and return a BCP-47 code "
    "(e.g. ml-IN Malayalam, en-IN English, hi-IN Hindi, ta-IN Tamil, te-IN Telugu). "
    "Respond with JSON only — no markdown, no explanation: "
    '{"transcript": "...", "language": "xx-XX"}. '
    'If no speech detected return {"transcript": "", "language": "ml-IN"}.'
)


def _pcm8k_to_wav16k(pcm_8k: bytes) -> bytes:
    """Upsample PCM16 8 kHz mono → 16 kHz, wrap in WAV container."""
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_16k)
    return buf.getvalue()


class GeminiSTT:
    """Transcribes audio via Gemini 2.5 Flash — no Google Cloud billing needed."""

    def __init__(self):
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)

    async def transcribe(self, audio_bytes: bytes) -> tuple[str, str, float]:
        """
        Returns (transcript, language_code, confidence).
        audio_bytes: raw PCM16 @ 8 kHz mono.
        """
        t_start = time.monotonic()
        try:
            wav = _pcm8k_to_wav16k(audio_bytes)
            response = await self._client.aio.models.generate_content(
                model=_MODEL,
                contents=[
                    types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                    _PROMPT,
                ],
            )
            latency_ms = int((time.monotonic() - t_start) * 1000)
            raw = (response.text or "").strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

            try:
                data = json.loads(raw)
                transcript = str(data.get("transcript", "")).strip()
                language = str(data.get("language", "ml-IN")).strip() or "ml-IN"
            except (json.JSONDecodeError, AttributeError):
                transcript = raw
                language = "ml-IN"

            logger.info("gemini_stt_ok", transcript=transcript[:100],
                        lang=language, latency_ms=latency_ms)
            return transcript, language, 0.9

        except Exception as e:
            logger.error("gemini_stt_failed", error=str(e))
            return "", "ml-IN", 0.0
