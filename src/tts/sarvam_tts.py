"""
Sarvam Bulbul v3 TTS provider.

Most realistic Indian-language voice. Handles Manglish and mixed
Malayalam+English naturally.

Input : text (str) + optional language code
Output: raw PCM16 mono @ 8 kHz bytes, or None on failure
"""
from __future__ import annotations

import base64
import time
from typing import Optional

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_MAX_TEXT_LEN = 500  # Sarvam API limit

# Best Bulbul v3 speaker per language
_LANG_SPEAKER: dict[str, str] = {
    "ml-IN": "meera",
    "ta-IN": "pavithra",
    "hi-IN": "maitreyi",
    "kn-IN": "meera",
    "te-IN": "meera",
    "en-IN": "ananya",
    "manglish": "meera",
}


def _wav_to_pcm_8k_mono(wav_bytes: bytes) -> bytes:
    """Convert WAV bytes to raw PCM16 mono @ 8 kHz."""
    import wave
    import io
    import audioop

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        if channels == 2:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
        if sample_width != 2:
            pcm = audioop.lin2lin(pcm, sample_width, 2)
        if framerate != 8000:
            pcm, _ = audioop.ratecv(pcm, 2, 1, framerate, 8000, None)
        return pcm
    except Exception:
        return wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes


class SarvamTTS:
    """
    Sarvam Bulbul v3 text-to-speech.

    Requires SARVAM_API_KEY in environment / .env.
    Optionally override the speaker via SARVAM_TTS_SPEAKER setting.
    Returns raw PCM16 mono @ 8 kHz bytes (ready to stream to Exotel).
    """

    def __init__(self) -> None:
        self._api_key: str = getattr(settings, "SARVAM_API_KEY", "")
        self._default_speaker: str = getattr(settings, "SARVAM_TTS_SPEAKER", "")

    def _pick_speaker(self, language: str) -> str:
        """Return the best speaker for the given language code."""
        if self._default_speaker:
            return self._default_speaker
        return _LANG_SPEAKER.get(language, "meera")

    async def synthesize(self, text: str, language: str = "ml-IN") -> Optional[bytes]:
        """
        Convert text to raw PCM16 mono @ 8 kHz audio.

        Args:
            text:     text to synthesise (truncated at 500 chars)
            language: BCP-47 language code (default "ml-IN")

        Returns:
            raw PCM16 bytes, or None on failure
        """
        if not text:
            return None

        # Truncate to Sarvam's limit
        if len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN]

        speaker = self._pick_speaker(language)
        t_start = time.monotonic()

        payload = {
            "inputs": [text],
            "target_language_code": language,
            "speaker": speaker,
            "model": "bulbul:v3",
            "pitch": 0,
            "pace": 0.9,
            "loudness": 1.5,
            "speech_sample_rate": 8000,
            "enable_preprocessing": True,
            "eng_interpolation_wt": 123,
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(read=15.0, connect=3.0)
            ) as client:
                response = await client.post(
                    _SARVAM_TTS_URL,
                    headers={
                        "api-subscription-key": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

            latency_ms = int((time.monotonic() - t_start) * 1000)

            if response.status_code >= 400:
                logger.error(
                    "sarvam_tts_error",
                    error=f"HTTP {response.status_code}: {response.text[:300]}",
                )
                return None

            data = response.json()
            audios = data.get("audios", [])
            if not audios:
                logger.error("sarvam_tts_error", error="empty audios array in response")
                return None

            wav_bytes = base64.b64decode(audios[0])
            pcm_bytes = _wav_to_pcm_8k_mono(wav_bytes)

            logger.info(
                "sarvam_tts_ok",
                latency_ms=latency_ms,
                text_len=len(text),
                pcm_bytes=len(pcm_bytes),
            )
            return pcm_bytes

        except httpx.TimeoutException as exc:
            logger.error("sarvam_tts_error", error=f"Timeout: {exc}")
            return None
        except Exception as exc:
            logger.error("sarvam_tts_error", error=str(exc))
            return None
