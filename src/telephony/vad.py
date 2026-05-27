"""
Simple energy-based Voice Activity Detection.

For production, replace with WebRTC VAD or Silero VAD.
This implementation is sufficient for detecting speech vs silence
in telephony audio (8kHz mulaw).
"""

from __future__ import annotations

import audioop
import struct
from typing import Optional


class SimpleVAD:
    """
    Energy-based VAD.
    Works on mulaw-encoded 8kHz telephony audio.

    Thresholds tuned for typical phone call conditions.
    """

    SPEECH_THRESHOLD = 200      # RMS energy above this = speech
    SILENCE_THRESHOLD = 100     # RMS energy below this = silence

    def __init__(
        self,
        speech_threshold: int = SPEECH_THRESHOLD,
        silence_threshold: int = SILENCE_THRESHOLD,
    ):
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold

    def rms_energy(self, audio_bytes: bytes) -> float:
        """Calculate RMS energy of mulaw audio chunk."""
        try:
            # Decode mulaw to linear PCM first
            pcm = audioop.ulaw2lin(audio_bytes, 2)
            rms = audioop.rms(pcm, 2)
            return float(rms)
        except Exception:
            return 0.0

    def is_speech(self, audio_bytes: bytes) -> bool:
        """Returns True if chunk contains speech."""
        return self.rms_energy(audio_bytes) > self.speech_threshold

    def is_silence(self, audio_bytes: bytes) -> bool:
        """Returns True if chunk is silence."""
        return self.rms_energy(audio_bytes) < self.silence_threshold
