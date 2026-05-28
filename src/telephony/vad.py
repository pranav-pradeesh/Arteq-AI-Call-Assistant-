"""
Adaptive energy-based Voice Activity Detection.

Works on linear PCM 16-bit audio (Exotel Voicebot's native encoding).

The key improvement over a fixed threshold: we track a rolling background-noise
floor and require the caller's voice to exceed that floor by a significant ratio.
This handles noisy environments (TV on, family nearby, open office) where a fixed
threshold would mistake background audio for caller speech.

  Quiet room:  noise_floor ~150  → speech threshold ~375 RMS
  Noisy room:  noise_floor ~400  → speech threshold ~1000 RMS
  Phone noise: noise_floor ~600  → speech threshold ~1500 RMS

The caller speaks directly into the mic (RMS 1500-3000+); background voices
3-5 feet away are typically at 300-600 RMS, well below the adaptive threshold.
"""
from __future__ import annotations

import audioop


class SimpleVAD:
    """Adaptive energy-based VAD with rolling noise-floor tracking."""

    # Static fallback (used for backward compat and before adaptation kicks in)
    SPEECH_THRESHOLD = 400
    SILENCE_THRESHOLD = 200

    # Adaptive noise-floor settings
    _NOISE_ADAPT_ALPHA = 0.05     # exponential moving average rate
    _NOISE_ADAPT_ALPHA_FAST = 0.10  # faster rate for first 20 frames (cold start)
    _SPEECH_SNR_RATIO = 2.5       # speech must be 2.5× above noise floor
    _MIN_SPEECH_THRESHOLD = 350   # absolute minimum even in a quiet room
    _MAX_NOISE_FLOOR = 700        # cap: prevent threshold from rising too high
    _SILENCE_SNR = 1.3            # RMS < noise_floor × 1.3 → treated as silence

    def __init__(
        self,
        speech_threshold: int = SPEECH_THRESHOLD,
        silence_threshold: int = SILENCE_THRESHOLD,
    ):
        # Static thresholds kept for backward compat; adaptive ones take over
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold
        self._noise_floor: float = 200.0
        self._frame_count: int = 0

    # ── Core ─────────────────────────────────────────────────────────────────

    def rms_energy(self, pcm16_bytes: bytes) -> float:
        try:
            return float(audioop.rms(pcm16_bytes, 2))
        except Exception:
            return 0.0

    def update_noise_floor(self, rms: float) -> None:
        """
        Update the rolling noise-floor estimate.
        Only use frames that are plausibly background (not loud caller speech)
        so that the floor tracks ambient noise, not the caller's voice.
        Call this for every accumulate-phase chunk (i.e. bot is silent).
        Do NOT call during TTS playback — outgoing audio biases the estimate.
        """
        # Use faster alpha for the first 20 frames so we calibrate quickly
        alpha = (
            self._NOISE_ADAPT_ALPHA_FAST
            if self._frame_count < 20
            else self._NOISE_ADAPT_ALPHA
        )
        # Only adapt from frames that are below 1.8× current estimate to
        # avoid letting loud caller speech drag the noise floor up.
        if rms < self._noise_floor * 1.8 or self._frame_count < 20:
            self._noise_floor = (1 - alpha) * self._noise_floor + alpha * rms
            self._noise_floor = min(self._noise_floor, self._MAX_NOISE_FLOOR)
        self._frame_count += 1

    # ── Thresholds ────────────────────────────────────────────────────────────

    def effective_speech_threshold(self) -> float:
        """Dynamic threshold: noise_floor × SNR, but never below the minimum."""
        return max(self._noise_floor * self._SPEECH_SNR_RATIO, self._MIN_SPEECH_THRESHOLD)

    def effective_silence_threshold(self) -> float:
        """Dynamic silence threshold: slightly above the noise floor."""
        return self._noise_floor * self._SILENCE_SNR

    # ── Decisions ─────────────────────────────────────────────────────────────

    def is_speech(self, pcm16_bytes: bytes) -> bool:
        return self.rms_energy(pcm16_bytes) > self.effective_speech_threshold()

    def is_silence(self, pcm16_bytes: bytes) -> bool:
        return self.rms_energy(pcm16_bytes) < self.effective_silence_threshold()
