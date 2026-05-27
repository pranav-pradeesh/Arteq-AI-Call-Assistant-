"""
Simple energy-based Voice Activity Detection.

Works on linear PCM 16-bit audio (Exotel Voicebot's native encoding).
For production-grade VAD, swap in Silero or WebRTC.
"""
from __future__ import annotations

import audioop


class SimpleVAD:
    """Energy-based VAD on PCM16 audio chunks."""

    SPEECH_THRESHOLD = 400    # RMS for PCM16 8kHz — telephony floor
    SILENCE_THRESHOLD = 200

    def __init__(
        self,
        speech_threshold: int = SPEECH_THRESHOLD,
        silence_threshold: int = SILENCE_THRESHOLD,
    ):
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold

    def rms_energy(self, pcm16_bytes: bytes) -> float:
        try:
            return float(audioop.rms(pcm16_bytes, 2))
        except Exception:
            return 0.0

    def is_speech(self, pcm16_bytes: bytes) -> bool:
        return self.rms_energy(pcm16_bytes) > self.speech_threshold

    def is_silence(self, pcm16_bytes: bytes) -> bool:
        return self.rms_energy(pcm16_bytes) < self.silence_threshold
