"""
Google Cloud TTS Neural2 provider.

REST API: https://texttospeech.googleapis.com/v1/text:synthesize
Auth: ?key={GOOGLE_CLOUD_TTS_KEY} query param
Audio: LINEAR16 at 8000 Hz → direct raw PCM for Exotel
"""
from __future__ import annotations

import base64
import time
from typing import Optional

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger
from src.tts.voice_map import get_voice

logger = get_logger(__name__)

_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_MAX_CHARS = 5000  # Google TTS hard limit


class GoogleTTS:
    """Google Cloud TTS Neural2 — expressive multilingual synthesis."""

    async def synthesize(
        self,
        text: str,
        language: str = "ml-IN",
        speaking_rate: float = 0.9,
    ) -> Optional[bytes]:
        if not settings.GOOGLE_CLOUD_TTS_KEY:
            logger.error("google_tts_no_key")
            return None

        text = text[:_MAX_CHARS]
        voice_name = get_voice(language)
        # Extract language code from voice name (e.g. "ml-IN-Neural2-A" → "ml-IN")
        lang_code = "-".join(voice_name.split("-")[:2])

        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": lang_code,
                "name": voice_name,
            },
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": 8000,
                "speakingRate": speaking_rate,
                "pitch": 0.0,
                "volumeGainDb": 0.0,
            },
        }

        t_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
                resp = await client.post(
                    _TTS_URL,
                    params={"key": settings.GOOGLE_CLOUD_TTS_KEY},
                    json=payload,
                )
            latency_ms = int((time.monotonic() - t_start) * 1000)

            if resp.status_code >= 400:
                logger.error("google_tts_http_error",
                             status=resp.status_code, body=resp.text[:300],
                             voice=voice_name, language=language)
                return None

            data = resp.json()
            audio_content = data.get("audioContent")
            if not audio_content:
                logger.error("google_tts_empty_response", response=str(data)[:200])
                return None

            wav_bytes = base64.b64decode(audio_content)
            # Google returns LINEAR16 WAV. Strip the WAV header to get raw PCM.
            # Use the shared helper from engine.py which handles header parsing safely.
            from src.tts.engine import _wav_to_pcm_8k_mono
            pcm_bytes = _wav_to_pcm_8k_mono(wav_bytes)

            logger.info("google_tts_ok",
                        voice=voice_name, language=language,
                        text_len=len(text), latency_ms=latency_ms,
                        pcm_bytes=len(pcm_bytes))
            return pcm_bytes

        except httpx.TimeoutException:
            logger.error("google_tts_timeout", language=language, voice=voice_name)
            return None
        except Exception as e:
            logger.error("google_tts_error", error=str(e), language=language, voice=voice_name)
            return None
