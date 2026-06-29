"""
Arteq Hospital Voice Agent — LiveKit 1.5.x edition.

Full-featured AI receptionist for Kerala hospitals:
  • Silero VAD → Sarvam STT (Saarika, transcribes in the caller's own language)
  • Google AI Studio Gemini (default: gemini-2.5-flash-lite, via OpenAI-compatible base_url)
  • Sarvam TTS (Bulbul v3, Malayalam, "shubh" voice)
  • Acoustic Sensory Layer — detects patient distress from PCM stats
  • Function tools — book/cancel appointments, callbacks, SMS, emergency
  • Multi-tenant — room name = "{slug}-call-{uuid}", context from DB

Run:
  python livekit_agent.py dev      # development (auto-join)
  python livekit_agent.py start    # production worker pool

Required env vars:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
  SARVAM_API_KEY, GOOGLE_API_KEY
  DATABASE_URL
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("livekit.agents")

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    APIConnectOptions,
    EndpointingOptions,
    JobContext,
    PreemptiveGenerationOptions,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
)
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents import llm as agents_llm  # ChatContext, ChatMessage types
from livekit.plugins import noise_cancellation, openai, sarvam, silero
from openai import AsyncClient as _AsyncOpenAI  # raw SDK, for custom-header Sarvam client

load_dotenv()

# ── Sarvam Bulbul v3 TTS ────────────────────────────────────────────────────────
# livekit-plugins-sarvam 1.1.7 only knows bulbul:v2 and unconditionally sends
# `pitch` and `loudness`. Bulbul v3 rejects those two params (400). Subclass the
# stream to drop them while keeping v3's better Malayalam voices.

import base64 as _b64
import aiohttp as _aiohttp
from livekit.agents import (
    APIConnectionError as _APIConnErr,
    APIStatusError as _APIStatusErr,
    APITimeoutError as _APITimeoutErr,
)
from livekit.plugins.sarvam.tts import (
    TTS as _SarvamTTS,
    ChunkedStream as _SarvamChunkedStream,
    MODEL_SPEAKER_COMPATIBILITY as _SARVAM_COMPAT,
    logger as _sarvam_log,
)


# Unicode script ranges → Sarvam Bulbul v3 target_language_code. Bulbul speaks
# the text in the phonetics of this language, so it MUST match the script the
# LLM replied in — otherwise an English/Hindi/Tamil reply gets Malayalam
# phonetics. Detected per-utterance so one agent handles every caller language.
_SCRIPT_RANGES = [
    ("ml-IN", 0x0D00, 0x0D7F),  # Malayalam
    ("ta-IN", 0x0B80, 0x0BFF),  # Tamil
    ("te-IN", 0x0C00, 0x0C7F),  # Telugu
    ("kn-IN", 0x0C80, 0x0CFF),  # Kannada
    ("hi-IN", 0x0900, 0x097F),  # Devanagari (Hindi/Marathi)
    ("bn-IN", 0x0980, 0x09FF),  # Bengali
    ("gu-IN", 0x0A80, 0x0AFF),  # Gujarati
    ("pa-IN", 0x0A00, 0x0A7F),  # Gurmukhi (Punjabi)
    ("od-IN", 0x0B00, 0x0B7F),  # Odia
]


def _detect_tts_lang(text: str, fallback: str) -> str:
    from src.tts_normalize import detect_tts_lang
    return detect_tts_lang(text, fallback)


def _voice_for_lang(lang: str, default: str) -> str:
    from src.tts_normalize import voice_for_lang
    return voice_for_lang(lang, default)


def _split_mixed_script(text: str, fallback: str) -> list:
    from src.tts_normalize import split_mixed_script
    return split_mixed_script(text, fallback)


# Deterministic safety net for English-in-English: Bulbul v3 speaks code-mixed
# text correctly *when the English word is in Latin script*, so the only job here
# is to undo the cases where a weaker LLM transliterated a common English term
# into Malayalam despite the prompt rule. We map the bare forms back to Latin so
# Bulbul pronounces them in English. The prompt does the heavy lifting; this
# catches the high-frequency hospital terms the model most often slips on.
# Inflected forms (Malayalam suffixes glued on) are left to the prompt.
_TTS_EN_MAP = {
    "ഡോക്ടര്‍": "Doctor", "ഡോക്ടർ": "Doctor", "ഡോക്ടര്": "Doctor", "ഡോ.": "Doctor",
    "അപ്പോയിന്റ്മെന്റ്": "appointment", "അപ്പോയിന്റ്മെൻറ്": "appointment",
    "കാർഡിയോളജി": "cardiology", "ന്യൂറോളജി": "neurology",
    "ഓകെ": "OK", "ഇയെസ്": "Yes", "ഇയേസ്": "Yes", "നോ": "No",
    "സ്കാനിംഗ്": "scanning", "സ്കാനിങ്": "scanning",
    "റിപ്പോർട്ട്": "report", "ടോക്കൺ": "token", "കൗണ്ടർ": "counter",
}
# Longest-first so multi-token forms win over any shorter prefix match.
_TTS_EN_RE = re.compile("|".join(re.escape(k) for k in sorted(_TTS_EN_MAP, key=len, reverse=True)))

# Inflected forms: Malayalam glues case/postpositional suffixes straight onto a
# loanword (അപ്പോയിന്റ്മെന്റ് → അപ്പോയിന്റ്മെന്റിന് "for the appointment"). We
# absorb any trailing Malayalam letters after a known *long, unambiguous* root so
# the whole word becomes the English term. Restricted to long roots only — a
# short root like നോ ("no") is a prefix of നോക്കി ("looked") and would mis-fire,
# so short/ambiguous terms stay exact-match in _TTS_EN_MAP above.
_TTS_EN_STEMS = {
    "അപ്പോയിന്റ്മെന്റ": "appointment", "അപ്പോയിന്റ്മെൻറ": "appointment",
    "കാർഡിയോളജി": "cardiology", "ന്യൂറോളജി": "neurology",
    "സ്കാനി": "scanning", "റിപ്പോർട്ട": "report", "ഡോക്ടറ": "Doctor",
}
# stem + any run of Malayalam-block chars (the glued suffix). Longest stem first.
_TTS_EN_STEM_RE = re.compile(
    "(?:" + "|".join(re.escape(s) for s in sorted(_TTS_EN_STEMS, key=len, reverse=True)) + r")[ഀ-ൿ]*"
)
_TTS_EN_STEM_LOOKUP = sorted(_TTS_EN_STEMS, key=len, reverse=True)


def _stem_repl(m: "re.Match") -> str:
    word = m.group(0)
    for stem in _TTS_EN_STEM_LOOKUP:
        if word.startswith(stem):
            return " " + _TTS_EN_STEMS[stem] + " "
    return word


def _normalize_for_tts(text: str) -> str:
    from src.tts_normalize import normalize_for_tts
    return normalize_for_tts(text)


def _tts_cache_key(opts, text: str, lang: str, speaker: str) -> str:
    import hashlib
    raw = f"{opts.model}|{lang}|{speaker}|{opts.pace}|{opts.speech_sample_rate}|{text}"
    return "tts:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


class _BulbulV3ChunkedStream(_SarvamChunkedStream):
    async def _run(self, output_emitter) -> None:
        import hashlib
        from src.cache.store import tts_cache

        text = _normalize_for_tts(self._input_text)
        lang = _detect_tts_lang(text, self._opts.target_language_code)
        speaker = _voice_for_lang(lang, self._opts.speaker)

        # Two separate cache namespaces: WebSocket streaming produces MP3 chunks;
        # REST produces WAV. Mixing them would require tracking format per-entry,
        # so they use distinct key prefixes. WS cache value: {"mime":…, "chunks":[…]}.
        # REST cache value: [wav_bytes, …] (legacy list format).
        ws_key = "tts:ws:" + hashlib.md5(
            f"bulbul:v3|{lang}|{speaker}|{text}".encode("utf-8")
        ).hexdigest()
        rest_key = _tts_cache_key(self._opts, text, lang, speaker)

        ws_cached = tts_cache.get(ws_key)
        if ws_cached is not None:
            output_emitter.initialize(
                request_id="cache",
                sample_rate=self._tts.sample_rate,
                num_channels=self._tts.num_channels,
                mime_type=ws_cached["mime"],
            )
            for chunk in ws_cached["chunks"]:
                output_emitter.push(chunk)
            return

        rest_cached = tts_cache.get(rest_key)
        if rest_cached is not None:
            output_emitter.initialize(
                request_id="cache",
                sample_rate=self._tts.sample_rate,
                num_channels=self._tts.num_channels,
                mime_type="audio/wav",
            )
            for chunk in rest_cached:
                output_emitter.push(chunk)
            return

        # Mixed-script text (e.g. "Good morning! Welcome to Hospital, <native question>"):
        # split into per-script segments and synthesize each with the correct lang so
        # English words get English phonetics and native words get native phonetics.
        # This is the COMMON case for a Malayalam receptionist (native speech with
        # embedded English terms), so it must stream: synthesize each segment over the
        # WebSocket and push audio as it arrives, so the caller hears segment 1 while
        # the rest are still synthesizing — TTFB ≈ first-segment latency instead of the
        # whole utterance. Fall back to the seamless REST PCM-concat path on WS failure.
        segments = _split_mixed_script(text, lang)
        if len(segments) > 1:
            try:
                await self._run_multi_ws(output_emitter, segments, speaker, ws_key)
            except Exception as exc:
                _sarvam_log.warning(
                    "multi-segment WS TTS failed (%s), falling back to REST", exc
                )
                await self._run_multi_rest(output_emitter, segments, speaker, rest_key)
            return

        try:
            await self._run_ws(output_emitter, text, lang, speaker, ws_key)
        except Exception as exc:
            _sarvam_log.warning("TTS WebSocket failed (%s), falling back to REST", exc)
            await self._run_rest(output_emitter, text, lang, speaker, rest_key)

    async def _run_ws(
        self, output_emitter, text: str, lang: str, speaker: str, cache_key: str
    ) -> None:
        """WebSocket streaming TTS — first audio chunk plays while Bulbul generates the rest."""
        from sarvamai import AsyncSarvamAI, AudioOutput, EventResponse
        from src.cache.store import tts_cache, TTS_CACHE_TTL

        if self._tts._ws_client is None:
            self._tts._ws_client = AsyncSarvamAI(api_subscription_key=self._opts.api_key)

        chunks: list[bytes] = []
        initialized = False
        mime = "audio/mpeg"

        async with self._tts._ws_client.text_to_speech_streaming.connect(
            model="bulbul:v3",
            send_completion_event=True,
        ) as ws:
            # pace must be set here too — the WS streaming path is tried first, so
            # without it the speed setting would only apply to the REST fallback.
            await ws.configure(target_language_code=lang, speaker=speaker, pace=self._opts.pace)
            await ws.convert(text)
            await ws.flush()

            async for message in ws:
                if isinstance(message, AudioOutput):
                    chunk = _b64.b64decode(message.data.audio)
                    if not initialized:
                        output_emitter.initialize(
                            request_id="ws-stream",
                            sample_rate=self._tts.sample_rate,
                            num_channels=self._tts.num_channels,
                            mime_type=mime,
                        )
                        initialized = True
                    output_emitter.push(chunk)
                    chunks.append(chunk)
                elif isinstance(message, EventResponse):
                    if getattr(message.data, "event_type", "") == "final":
                        break

        if not initialized:
            raise RuntimeError("WebSocket TTS: no audio chunks received")
        tts_cache.set(cache_key, {"mime": mime, "chunks": chunks}, ttl=TTS_CACHE_TTL)

    async def _fetch_wav_bytes(self, text: str, lang: str, speaker: str) -> list:
        """Fetch decoded WAV bytes from Sarvam REST for one text segment."""
        payload = {
            "target_language_code": lang,
            "text": text,
            "speaker": speaker,
            "pace": self._opts.pace,
            "speech_sample_rate": self._opts.speech_sample_rate,
            "model": self._opts.model,
        }
        headers = {
            "api-subscription-key": self._opts.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with self._tts._ensure_session().post(
                url=self._opts.base_url,
                json=payload,
                headers=headers,
                timeout=_aiohttp.ClientTimeout(
                    total=self._conn_options.timeout,
                    sock_connect=self._conn_options.timeout,
                ),
            ) as res:
                if res.status != 200:
                    error_text = await res.text()
                    _sarvam_log.error("Sarvam TTS REST error: %s - %s", res.status, error_text)
                    raise _APIStatusErr(
                        message=f"Sarvam TTS API Error: {error_text}", status_code=res.status
                    )
                response_json = await res.json()
                audios = response_json.get("audios", [])
                if not audios or not isinstance(audios, list):
                    raise _APIConnErr("Sarvam TTS API response invalid: no audio data")
                return [_b64.b64decode(b64) for b64 in audios]
        except asyncio.TimeoutError as e:
            raise _APITimeoutErr("Sarvam TTS API request timed out") from e
        except _aiohttp.ClientError as e:
            raise _APIConnErr(f"Sarvam TTS API connection error: {e}") from e

    async def _run_rest(
        self, output_emitter, text: str, lang: str, speaker: str, cache_key: str
    ) -> None:
        """REST TTS fallback — waits for full synthesis before any audio plays."""
        from src.cache.store import tts_cache, TTS_CACHE_TTL

        decoded = await self._fetch_wav_bytes(text, lang, speaker)
        output_emitter.initialize(
            request_id="rest",
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/wav",
        )
        for chunk in decoded:
            output_emitter.push(chunk)
        tts_cache.set(cache_key, decoded, ttl=TTS_CACHE_TTL)

    async def _run_multi_ws(
        self, output_emitter, segments: list, speaker: str, cache_key: str
    ) -> None:
        """Stream mixed-script text segment-by-segment over the WebSocket.

        Each segment is synthesized with its own language code (so English words
        use English phonetics and native-script words use native phonetics) and its
        audio chunks are pushed the moment they arrive. The first segment starts
        playing while later segments are still synthesizing, so time-to-first-byte
        is roughly the first segment's latency rather than the whole utterance's.

        Synthesis is faster than realtime, so pushing each segment right after the
        previous one keeps the emitter buffer ahead of playback — the segments play
        back as one continuous stream with no gap. Raises on any failure so the
        caller can fall back to the seamless REST PCM-concat path.
        """
        from sarvamai import AsyncSarvamAI, AudioOutput, EventResponse
        from src.cache.store import tts_cache, TTS_CACHE_TTL

        if self._tts._ws_client is None:
            self._tts._ws_client = AsyncSarvamAI(api_subscription_key=self._opts.api_key)

        chunks: list[bytes] = []
        initialized = False
        mime = "audio/mpeg"

        for seg_text, seg_lang in segments:
            seg_speaker = _voice_for_lang(seg_lang, speaker)
            async with self._tts._ws_client.text_to_speech_streaming.connect(
                model="bulbul:v3",
                send_completion_event=True,
            ) as ws:
                await ws.configure(
                    target_language_code=seg_lang, speaker=seg_speaker, pace=self._opts.pace
                )
                await ws.convert(seg_text)
                await ws.flush()

                async for message in ws:
                    if isinstance(message, AudioOutput):
                        chunk = _b64.b64decode(message.data.audio)
                        if not initialized:
                            output_emitter.initialize(
                                request_id="ws-multi",
                                sample_rate=self._tts.sample_rate,
                                num_channels=self._tts.num_channels,
                                mime_type=mime,
                            )
                            initialized = True
                        output_emitter.push(chunk)
                        chunks.append(chunk)
                    elif isinstance(message, EventResponse):
                        if getattr(message.data, "event_type", "") == "final":
                            break

        if not initialized:
            raise RuntimeError("multi-segment WS TTS: no audio chunks received")
        # Cache under the WS key (full text) so replays hit the streaming-format
        # cache at the top of _run, same as the single-segment WS path.
        tts_cache.set(cache_key, {"mime": mime, "chunks": chunks}, ttl=TTS_CACHE_TTL)

    async def _run_multi_rest(
        self, output_emitter, segments: list, speaker: str, cache_key: str
    ) -> None:
        """REST fallback: synthesize mixed-script text segment-by-segment, concatenate WAV PCM.

        Each segment is synthesized with its own language code so English words
        use English phonetics and native-script words use native phonetics.
        The joined PCM is pushed as a single seamless audio stream. Slower than the
        WS path (waits for full synthesis before any audio) but gap-free.
        """
        import io
        import wave
        from src.cache.store import tts_cache, TTS_CACHE_TTL

        all_pcm = b""
        sr = nc = sw = None

        for seg_text, seg_lang in segments:
            seg_speaker = _voice_for_lang(seg_lang, speaker)
            wav_chunks = await self._fetch_wav_bytes(seg_text, seg_lang, seg_speaker)
            for wav_bytes in wav_chunks:
                with io.BytesIO(wav_bytes) as buf:
                    with wave.open(buf, "rb") as wf:
                        if sr is None:
                            sr = wf.getframerate()
                            nc = wf.getnchannels()
                            sw = wf.getsampwidth()
                        all_pcm += wf.readframes(wf.getnframes())

        if not all_pcm or sr is None:
            raise _APIConnErr("Multi-segment TTS: no audio data received")

        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setnchannels(nc)
            wf.setsampwidth(sw)
            wf.setframerate(sr)
            wf.writeframes(all_pcm)
        combined = out.getvalue()

        output_emitter.initialize(
            request_id="multi-seg",
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/wav",
        )
        output_emitter.push(combined)
        tts_cache.set(cache_key, [combined], ttl=TTS_CACHE_TTL)


# Bulbul v3 voice roster (Sarvam docs). v3 rejects pitch, loudness and
# enable_preprocessing; its native sample rate is 24000 Hz (v2 used 22050).
_BULBUL_V3_SPEAKERS = [
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
]

# Teach the upstream (v2-only) plugin about v3 so its built-in speaker check
# validates against the real v3 roster instead of logging "unknown model" and
# skipping validation on every construction.
_SARVAM_COMPAT["bulbul:v3"] = {
    "all": list(_BULBUL_V3_SPEAKERS),
    "female": [],
    "male": [],
}


# Talking speed for Bulbul TTS. `pace` is Sarvam's speed multiplier — 1.0 is the
# natural, clearest default (best pronunciation). Env-tunable via TTS_PACE; raise
# above 1.0 only if you want Arya to speak faster. Must be applied on BOTH the
# WebSocket and REST paths (and every TTS instance) or the prewarm cache — whose
# key includes pace — won't match the live voice.
_TTS_PACE = float(os.getenv("TTS_PACE", "1.0"))


def _build_stt(lang):
    """Sarvam Saarika STT, with an OpenAI Whisper fallback if OPENAI_API_KEY is
    set — so a Sarvam credit lapse (402) doesn't kill the call."""
    primary = sarvam.STT(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        model=os.getenv("SARVAM_STT_MODEL", "saarika:v2.5"),
        language=lang,
    )
    if os.getenv("OPENAI_API_KEY"):
        try:
            from livekit.plugins import openai as _oai
            from livekit.agents import stt as _stt_mod
            backup = _oai.STT(model="whisper-1", language="ml")
            return _stt_mod.FallbackAdapter([primary, backup])
        except Exception as _e:
            print(f"[arteq] STT fallback unavailable: {_e}", file=sys.stderr)
    return primary


def _build_tts(lang):
    """Bulbul v3 TTS, with an OpenAI TTS fallback if OPENAI_API_KEY is set."""
    primary = BulbulV3TTS(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        target_language_code=lang,
        speaker="priya",
        pace=_TTS_PACE,
    )
    if os.getenv("OPENAI_API_KEY"):
        try:
            from livekit.plugins import openai as _oai
            from livekit.agents import tts as _tts_mod
            backup = _oai.TTS(model="gpt-4o-mini-tts", voice="shimmer")
            return _tts_mod.FallbackAdapter([primary, backup])
        except Exception as _e:
            print(f"[arteq] TTS fallback unavailable: {_e}", file=sys.stderr)
    return primary


class BulbulV3TTS(_SarvamTTS):
    """Sarvam Bulbul v3 TTS.

    The upstream plugin models only bulbul:v2 — it defaults to a v2 speaker at
    22050 Hz and its ChunkedStream always sends pitch/loudness. This subclass
    targets v3 purely:
      - forces model="bulbul:v3" and the v3-native 24000 Hz sample rate,
      - validates the speaker against the v3 roster (via the parent, now that v3
        is registered) — a bad voice fails fast instead of a raw 400,
      - emits a payload with ONLY v3-accepted fields (no pitch / loudness /
        enable_preprocessing) through _BulbulV3ChunkedStream.
    """

    def __init__(self, *, speaker: str = "priya", speech_sample_rate: int = 24000, **kwargs):
        kwargs.pop("model", None)   # v3 is enforced; ignore any caller override
        super().__init__(
            model="bulbul:v3",
            speaker=speaker,
            speech_sample_rate=speech_sample_rate,
            **kwargs,
        )
        self._ws_client = None  # AsyncSarvamAI, lazily created on first WS TTS request

    def synthesize(self, text: str, *, conn_options=None):
        from livekit.agents import DEFAULT_API_CONNECT_OPTIONS
        if conn_options is None:
            conn_options = DEFAULT_API_CONNECT_OPTIONS
        return _BulbulV3ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


# ── Constants ─────────────────────────────────────────────────────────────────

# Conversation memory window: keep the system prompt + the last N context items.
# IMPORTANT: "items" include tool calls AND tool results, not just spoken turns —
# a single booking step (check_availability → book_appointment) can add 3–4 items,
# so a small window made the agent forget what the caller said a moment earlier
# ("no continuity"). 40 items comfortably holds a whole hospital call. The per-turn
# cost is dominated by the large system prompt re-sent every turn, so retaining
# more short history messages is cheap. Override with MAX_CTX_ITEMS if needed.
_MAX_CTX = int(os.getenv("MAX_CTX_ITEMS", "40"))

# ── Realtime turn-taking knobs ─────────────────────────────────────────────────
# How fast Arya decides the caller has finished and starts replying — the single
# biggest lever on conversational "realtime" feel (it sets the EoU portion of
# per-turn latency). LOWER = snappier, but more risk of cutting a caller off in a
# natural mid-sentence pause; the defaults are a safe balance for elderly /
# Malayalam callers. Tune on REAL calls via env, no redeploy:
#   VAD_MIN_SILENCE        end-of-speech silence before turn-end (s). The main
#                          dial — try 0.10–0.15 for a snappier feel; raise back
#                          toward 0.2 if callers report being cut off.
#   ENDPOINTING_MIN_DELAY  extra post-speech wait before the LLM fires (s).
#   ENDPOINTING_MAX_DELAY  cap so a slow speaker pausing mid-thought isn't cut off.
#   VAD_MIN_SPEECH         min speech to count as a turn (s) — filters noise bursts.
#   VAD_ACTIVATION_THRESHOLD  Silero speech-prob gate (phone/SIP audio scores low,
#                          so don't raise this much or real speech is dropped).
_VAD_MIN_SILENCE = float(os.getenv("VAD_MIN_SILENCE", "0.2"))
_VAD_MIN_SPEECH = float(os.getenv("VAD_MIN_SPEECH", "0.3"))
_VAD_ACTIVATION = float(os.getenv("VAD_ACTIVATION_THRESHOLD", "0.5"))
_ENDPOINT_MIN_DELAY = float(os.getenv("ENDPOINTING_MIN_DELAY", "0.2"))
_ENDPOINT_MAX_DELAY = float(os.getenv("ENDPOINTING_MAX_DELAY", "3.0"))

_WATCHDOG_FAREWELLS = {
    "ml-IN":      "ക്ഷമിക്കണം, maximum call time ആയി. വേറേ കാര്യം ഉണ്ടെങ്കിൽ please തിരിച്ചു call ചെയ്യൂ. നന്ദി, goodbye!",
    "hi-IN":      "क्षमा करें, कॉल का अधिकतम समय समाप्त हो गया। ज़रूरत हो तो दोबारा कॉल करें। धन्यवाद, अलविदा!",
    "ta-IN":      "மன்னிக்கவும், அழைப்பின் அதிகபட்ச நேரம் முடிந்தது. தேவையெனில் திரும்ப அழைக்கவும். நன்றி!",
    "te-IN":      "క్షమించండి, గరిష్ట కాల్ సమయం ముగిసింది. అవసరమైతే తిరిగి కాల్ చేయండి. ధన్యవాదాలు!",
    "kn-IN":      "ಕ್ಷಮಿಸಿ, ಗರಿಷ್ಠ ಕರೆ ಸಮಯ ಮುಗಿದಿದೆ. ಇನ್ನಷ್ಟು ಸಹಾಯ ಬೇಕಾದರೆ ಮತ್ತೆ ಕರೆ ಮಾಡಿ. ಧನ್ಯವಾದ!",
    "bn-IN":      "দুঃখিত, সর্বোচ্চ কল সময় শেষ হয়েছে। প্রয়োজন হলে আবার কল করুন। ধন্যবাদ!",
    "gu-IN":      "માફ કરશો, મહત્તમ કૉલ સમય પૂરો થયો. ફરીથી ફોન કરો. આભાર!",
    "pa-IN":      "ਮਾਫ਼ ਕਰਨਾ, ਵੱਧ ਤੋਂ ਵੱਧ ਕਾਲ ਸਮਾਂ ਖਤਮ ਹੋ ਗਿਆ। ਲੋੜ ਹੋਵੇ ਤਾਂ ਦੁਬਾਰਾ ਕਾਲ ਕਰੋ। ਧੰਨਵਾਦ!",
    "od-IN":      "କ୍ଷମା କରନ୍ତୁ, ସର୍ବାଧିକ call ସମୟ ଶେଷ ହୋଇଛି। ଆବଶ୍ୟକ ହେଲେ ପୁଣି call କରନ୍ତୁ। ଧନ୍ୟବାଦ!",
    "mr-IN":      "क्षमस्व, कमाल कॉल वेळ संपली. गरज असल्यास पुन्हा कॉल करा. धन्यवाद!",
    "en-IN":      "Sorry, we've reached the maximum call time. Please call back if you need anything else. Thank you, goodbye!",
}

# ── Ambient background audio ──────────────────────────────────────────────────
# Published as a second LocalAudioTrack from the agent participant. LiveKit's
# SIP gateway mixes all tracks from a participant before forwarding to PSTN,
# so the caller hears a constant low-level ambient noise that makes the AI feel
# like a real hospital reception rather than a silent VoIP line.
_AMBIENT_SAMPLE_RATE = 16000
_AMBIENT_FRAME_SAMPLES = 320  # 20 ms at 16 kHz — standard WebRTC frame size

# Pre-generate 5 s of softened white noise (fixed seed → deterministic across
# worker restarts). A 7-sample boxcar smooths out the harshest high-frequency
# content so it sounds closer to HVAC ventilation than raw hiss.
# Level is the fraction of int16 max (0.0–1.0) and is env-tunable via
# AMBIENT_NOISE_LEVEL: lower = quieter background, 0 = silent line (track not
# published at all). Default 0.02 (2 %) — subtle presence well under speech;
# the original 0.06 was noticeably loud on a real call.
_AMBIENT_LEVEL = min(max(float(os.getenv("AMBIENT_NOISE_LEVEL", "0.02")), 0.0), 1.0)
_rng = np.random.default_rng(seed=0)
_raw = _rng.standard_normal(5 * _AMBIENT_SAMPLE_RATE + 7).astype(np.float64)
_raw = np.convolve(_raw, np.ones(7) / 7, mode="valid")
_raw /= np.abs(_raw).max()
_AMBIENT_BUF = (_raw * _AMBIENT_LEVEL * 32767).astype(np.int16)
del _raw, _rng

_DTMF = {
    "1": "OPD timing please",
    "2": "emergency help needed",
    "3": "lab test timings",
    "4": "pharmacy location and timing",
    "5": "billing inquiry",
    "0": "transfer to reception desk",
    "*": "please repeat that",
    "#": "thank you goodbye",
}

# Phrases that mean "say it again" in English, Malayalam, Hindi, Tamil.
# Matched (as substring) against the lowercase caller utterance to replay
# the last agent TTS without a new LLM round-trip.
_REPEAT_PATTERNS = [
    "repeat", "say again", "what did you say", "didn't hear", "couldn't hear",
    "pardon", "come again", "one more time", "repeat that", "say that again",
    "what was that", "didn't catch", "couldn't catch",
    # Malayalam
    "വീണ്ടും", "ആവർത്തി", "കേട്ടില്ല", "മനസ്സിലായില്ല", "പറഞ്ഞത്", "ഒന്നുകൂടി",
    # Hindi
    "दोबारा", "फिर से", "समझ नहीं", "सुना नहीं",
    # Tamil
    "மீண்டும்", "திரும்ப", "கேட்கவில்லை",
]

# Carrier / iPhone voicemail greetings — if an OUTBOUND call is answered by a
# voicemail machine (not a human), end the call and let the retry rule try again.
_VOICEMAIL_PATTERNS = [
    "leave a message", "after the tone", "after the beep", "at the tone",
    "record your message", "record your voice", "leave your message",
    "leave a message after", "voicemail", "voice mail", "please leave",
    "you have reached", "not available", "is unavailable", "not reachable",
    "currently not available", "person you are trying", "unable to take your call",
    "call is being forwarded", "call has been forwarded", "please record",
]


def _build_llm(premium: bool = True):
    """Single conversational brain. Sarvam-30B is NOT used as an LLM.

    LLM_PROVIDER selects the brain (default "gemini"):
      • "gemini" → Google AI Studio Gemini (default GOOGLE_MODEL=gemini-2.5-flash-lite)
                   via Google's own OpenAI-compatible endpoint. Low time-to-first-token
                   AND strong Malayalam. flash-lite is the cheapest tier; raise
                   GOOGLE_MODEL to gemini-2.5-flash for higher quality. REQUIRES a
                   FUNDED Google API key (real prepaid billing or Vertex) — the Google
                   free-trial credit CANNOT pay for the Gemini API and 429s every turn.
                   There is NO automatic fallback now, so an unfunded key drops turns;
                   use LLM_PROVIDER=openrouter if you don't have funded Google billing.
      • "openrouter" → OpenRouter, an OpenAI-compatible gateway routing to
                   OPENROUTER_MODEL (default google/gemini-2.5-flash-lite). ONE key
                   (OPENROUTER_API_KEY) pays for any model OpenRouter hosts, so it
                   avoids the funded-Google-billing requirement of direct Gemini.
      • "groq"   → Groq llama-3.3-70b-versatile. Lowest TTFT, but OPT-IN: requires
                   an ACTIVE Groq developer plan, and its Malayalam is weaker.

    Sarvam-30B was removed as a conversational brain — it answered terse one-word
    Malayalam turns poorly. (Sarvam is still used for STT/Saarika and TTS/Bulbul;
    that is unaffected.) A single brain now answers every turn, with no error
    fallback: the unknown/legacy "sarvam" provider value resolves to Gemini.
    """
    google_key = os.getenv("GOOGLE_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    # A phone receptionist speaks 1–2 short sentences, so a tight reply ceiling
    # keeps generation time (and therefore latency) low. Shared by all brains.
    _max_tokens = int(os.getenv("LLM_MAX_TOKENS", "300"))

    def _openrouter() -> Optional[object]:
        if not openrouter_key:
            return None
        # OpenRouter is OpenAI-compatible (Bearer auth). The optional HTTP-Referer
        # and X-Title headers attribute traffic to this app on OpenRouter's
        # dashboards/leaderboards; both are no-ops if you leave the defaults.
        return openai.LLM(
            model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
            temperature=0.4,
            max_completion_tokens=_max_tokens,
            client=_AsyncOpenAI(
                api_key=openrouter_key,
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                default_headers={
                    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://arteq.ai"),
                    "X-Title": os.getenv("OPENROUTER_APP_NAME", "Arteq AI Call Assistant"),
                },
            ),
        )

    def _gemini() -> Optional[object]:
        if not google_key:
            return None
        return openai.LLM(
            model=os.getenv("GOOGLE_MODEL", "gemini-2.5-flash-lite"),
            temperature=0.4,
            max_completion_tokens=_max_tokens,
            client=_AsyncOpenAI(
                api_key=google_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            ),
        )

    def _groq() -> Optional[object]:
        if not groq_key:
            return None
        return openai.LLM(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0.4,
            max_completion_tokens=_max_tokens,
            client=_AsyncOpenAI(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
            ),
        )

    provider = (os.getenv("LLM_PROVIDER", "gemini") or "gemini").strip().lower()
    # Sarvam is no longer a conversational brain. An unrecognised value — including
    # the legacy "sarvam" — resolves to Gemini, the default brain.
    build = {
        "gemini": _gemini,
        "openrouter": _openrouter,
        "groq": _groq,
    }.get(provider, _gemini)

    llm = build()
    if llm is None:
        raise RuntimeError(
            f"LLM_PROVIDER={provider!r} selected but its API key is not set. "
            "Set GOOGLE_API_KEY (LLM_PROVIDER=gemini), "
            "OPENROUTER_API_KEY (LLM_PROVIDER=openrouter), or GROQ_API_KEY (LLM_PROVIDER=groq)."
        )
    # Single brain, no FallbackAdapter — Sarvam is intentionally not parked behind it.
    return llm


# ==============================================================================
# Acoustic Sensory Layer
# ==============================================================================

class AcousticSensoryLayer:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._rms: list[float] = []
        self._zcr: list[float] = []

    def feed(self, frame: rtc.AudioFrame) -> None:
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float64)
        if pcm.size == 0:
            return
        self._rms.append(float(np.sqrt(np.mean(pcm ** 2))))
        self._zcr.append(float(np.count_nonzero(np.diff(pcm > 0))))

    def metadata(self) -> str:
        if not self._rms:
            return ""
        avg_vol = float(np.mean(self._rms))
        avg_zcr = float(np.mean(self._zcr))
        vol_var = float(np.var(self._rms))
        zcr_var = float(np.var(self._zcr))
        vol = "HIGH" if avg_vol > 1500 else ("LOW" if avg_vol < 300 else "NORMAL")
        pit = "HIGH" if avg_zcr > 80 else ("LOW" if avg_zcr < 30 else "NORMAL")
        stb = "TREMBLING" if (zcr_var > 400 or vol_var > 50_000) else "STEADY"
        if vol == "NORMAL" and pit == "NORMAL" and stb == "STEADY":
            return ""
        return f"[SENSORY: VOL={vol}, PITCH={pit}, TENSION={stb}]"


# ==============================================================================
# Hospital context helpers
# ==============================================================================

async def _resolve_call_target(room_name: str) -> tuple[str, dict, Optional[dict]]:
    """Resolve room -> (hospital_id, features, tenant).

    Looks the slug up in the control-DB tenant registry. If that tenant has its
    OWN database (db_url), binds this call's async context to that DB so every
    subsequent query routes there. The hospital row is then resolved inside the
    correct database. Falls back to single-DB / settings.HOSPITAL_ID on miss.
    """
    slug = room_name.split("-call-")[0] if "-call-" in room_name else room_name
    from src.config.settings import settings
    features: dict = {}
    tenant: Optional[dict] = None

    try:
        from src.tenancy import registry
        from src.db.queries import set_tenant_db_url
        tenant = await registry.get_tenant(slug.lower())
        if tenant:
            features = tenant.get("features", {}) or {}
            if tenant.get("db_url"):
                set_tenant_db_url(tenant["db_url"])
    except Exception as exc:
        print(f"[warn] tenant registry lookup failed: {exc}", file=sys.stderr)

    try:
        from src.db.queries import get_pool
        pool = await get_pool()   # tenant pool if bound above, else control
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM hospitals WHERE "
                "slug=$1 OR LOWER(REPLACE(name,' ','-'))=$1 LIMIT 1",
                slug.lower(),
            )
            if row:
                hospital_id = str(row["id"])
            else:
                # Slug miss (e.g. the /talk "default" room). Prefer the configured
                # HOSPITAL_ID, but if that row no longer exists (e.g. the demo
                # tenant was deleted) fall back to the first active hospital so the
                # agent never greets with a null context ("welcome to the hospital").
                exists = await conn.fetchval(
                    "SELECT 1 FROM hospitals WHERE id = $1", settings.HOSPITAL_ID
                )
                if exists:
                    hospital_id = settings.HOSPITAL_ID
                else:
                    fid = await conn.fetchval(
                        "SELECT id FROM hospitals WHERE active ORDER BY created_at LIMIT 1"
                    )
                    hospital_id = str(fid) if fid else settings.HOSPITAL_ID
    except Exception as exc:
        print(f"[warn] hospital ID lookup failed: {exc}", file=sys.stderr)
        hospital_id = settings.HOSPITAL_ID

    return hospital_id, features, tenant


async def _load_hospital_ctx(hospital_id: str):
    try:
        from src.db.queries import get_or_load_hospital_context
        return await get_or_load_hospital_context(hospital_id)
    except Exception as exc:
        print(f"[warn] hospital context load failed: {exc}", file=sys.stderr)
        return None


async def _load_patient_profile(caller_phone: str, hospital_id: str) -> Optional[dict]:
    try:
        from src.db.queries import get_appointments_by_phone
        appts = await get_appointments_by_phone(caller_phone, hospital_id)
        if not appts:
            return None
        return {
            "name": appts[0].get("patient_name", ""),
            "history": [
                {
                    "doctor": a.get("doctor_name", ""),
                    "dept": a.get("dept_name", ""),
                    "slot": str(a["slot_time"])[:16] if a.get("slot_time") else "",
                    "status": a.get("status", ""),
                }
                for a in appts[:3]
            ],
        }
    except Exception:
        return None


# ==============================================================================
# System prompt builder
# ==============================================================================

# Per-language grammar hints. Only the hospital's configured language is injected
# into the prompt (English/Manglish/Malayalam are handled by their own sections),
# so a Malayalam hospital doesn't pay the token (and latency) cost of 8 other
# languages' grammar on every turn.
_GRAMMAR_BY_LANG = {
    "hi-IN": 'Hindi: "आप" (formal). Verb agrees with gender; masculine default if unknown. Time: "सुबह दस बजे". Spoken question tag "ना?"/"क्या?".',
    "mr-IN": 'Marathi: "तुम्ही" (formal). Time: "सकाळी दहा वाजता". Spoken question tag "ना?".',
    "ta-IN": 'Tamil: "நீங்கள்" (formal). Questions end "-ஆ?". Time: "காலை 10 மணி". Say "வாங்க" not "வாரும்"; never "நீர்".',
    "te-IN": 'Telugu: "మీరు" (formal). Questions end "-ఆ?". Time: "ఉదయం 10 గంటలు". Everyday speech, not literary.',
    "kn-IN": 'Kannada: "ನೀವು" (formal). Questions end "-ಆ?". Time: "ಬೆಳಿಗ್ಗೆ 10 ಗಂಟೆ". Use "-ರಿ" honorific.',
    "bn-IN": 'Bengali: "আপনি" (formal). Questions add "কি?". Time: "সকাল দশটা". Verb "-চ্ছেন"/"-লেন".',
    "gu-IN": 'Gujarati: "આપ" (formal). Time: "સવારે દસ". Questions: "-ને?".',
    "pa-IN": 'Punjabi: "ਆਪ" (formal). Time: "ਸਵੇਰੇ ਦਸ ਵਜੇ". Respectful suffix "-ਜੀ".',
    "od-IN": 'Odia: "ଆପଣ" (formal). Time: "ସକାଳ ୧୦ ଟା". Questions end "-କି?".',
}


def _build_prompt(hospital_ctx, outbound_context: Optional[dict]) -> str:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(_IST)
    _DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    day_name = _DAYS[(now.weekday() + 1) % 7]
    time_str = now.strftime("%H:%M")
    date_iso = now.strftime("%Y-%m-%d")

    # Only inject the hospital's own language grammar (keeps the per-turn prompt
    # small → lower LLM time-to-first-token). Malayalam/English/Manglish are
    # covered by their dedicated sections, so ml-IN adds nothing here.
    _agent_lang = (getattr(hospital_ctx, "agent_language", "") or "ml-IN") if hospital_ctx else "ml-IN"
    _g = _GRAMMAR_BY_LANG.get(_agent_lang, "")
    grammar_block = ("GRAMMAR — speak like a real native, not a translation:\n" + _g) if _g else ""

    if hospital_ctx:
        try:
            from src.ai.groq_brain import _build_hospital_summary
            hosp_block = _build_hospital_summary(hospital_ctx)
        except Exception:
            hosp_block = f"Hospital: {hospital_ctx.name}"
        from src.tts_normalize import name_for_lang
        _lang = getattr(hospital_ctx, "agent_language", "ml-IN") or "ml-IN"
        hosp_name = name_for_lang(hospital_ctx.name, hospital_ctx.name_ml or "", _lang)
        _clo = hospital_ctx.closure_for(now.date())
        if _clo and _clo.get("closed", True):
            open_status = f"CLOSED TODAY — {_clo.get('reason') or 'holiday'}"
        elif _clo and _clo.get("open_time"):
            open_status = (f"OPEN {_clo['open_time']}–{_clo.get('close_time','')} "
                           f"(special hours: {_clo.get('reason') or 'holiday'})")
        else:
            dow = (now.weekday() + 1) % 7
            hours = hospital_ctx.hours_for_day(dow)
            if hours:
                open_t, close_t = hours
                open_status = (
                    f"OPEN {open_t}–{close_t}"
                    if open_t <= time_str <= close_t
                    else f"CLOSED now (opens {open_t})"
                )
            else:
                open_status = "CLOSED today"
    else:
        hosp_block = "Hospital information not available."
        hosp_name = "the hospital"
        open_status = "Unknown"

    outbound_block = ""
    if outbound_context:
        call_type = outbound_context.get("call_type", "")
        pname = outbound_context.get("patient_name", "")
        dname = outbound_context.get("doctor_name", "")
        date  = outbound_context.get("appointment_date", "")
        ttime = outbound_context.get("appointment_time", "")
        if call_type == "confirmation":
            outbound_block = (
                f"\nOUTBOUND CONFIRMATION CALL:\n"
                f"You are calling to confirm {pname}'s EXISTING appointment with Dr. {dname} "
                f"on {date} at {ttime}.\n"
                "First sentence: state the appointment and ask if they can attend.\n"
                "If YES → call confirm_appointment (this confirms the existing booking — "
                "do NOT call book_appointment, that would create a duplicate).\n"
                "If they want a different time → use reschedule_appointment.\n"
                "If they can't make it at all → use cancel_appointment.\n"
            )
        elif call_type == "doctor_availability":
            dstatus = outbound_context.get("doctor_status", "")
            if dstatus == "unavailable":
                avail_line = (
                    f"Dr. {dname} is UNAVAILABLE today. Apologise, explain the doctor "
                    "cannot see them, and offer to reschedule with reschedule_appointment."
                )
            elif dstatus == "delayed":
                avail_line = (
                    f"Dr. {dname} is running DELAYED today. Let them know there will be a "
                    "wait and ask if they still wish to come or prefer to reschedule."
                )
            else:
                avail_line = (
                    f"Dr. {dname} is AVAILABLE and on schedule today. Reassure them their "
                    f"appointment at {ttime} is on, and ask if they have any questions."
                )
            outbound_block = (
                f"\nOUTBOUND DOCTOR-AVAILABILITY CALL:\n"
                f"You are calling {pname} on the day of their appointment with Dr. {dname}"
                f"{(' on ' + date) if date else ''}.\n"
                f"{avail_line}\n"
                "Keep it brief and clear.\n"
            )
        elif call_type == "reminder":
            outbound_block = (
                f"\nOUTBOUND REMINDER CALL:\n"
                f"Reminding {pname} of appointment with Dr. {dname} on {date} at {ttime}.\n"
                "Keep it brief — just the reminder and ask if there are any questions.\n"
            )
        elif call_type == "callback":
            outbound_block = (
                f"\nOUTBOUND CALLBACK:\n"
                f"Calling {pname} back as requested. Ask how you can help today.\n"
            )
        elif call_type == "followup":
            outbound_block = (
                f"\nOUTBOUND FOLLOW-UP:\n"
                f"Calling {pname} 3 days after their appointment with Dr. {dname}.\n"
                "Ask how they are feeling and if they need anything.\n"
            )

    return f"""You are the voice receptionist for {hosp_name}. You have NO personal name — never introduce yourself with one and never invent one. Identify only as {hosp_name}. If a caller asks who you are or your name, say you are the reception assistant at {hosp_name}.

NATURAL CONVERSATION (most important): Talk like a real human receptionist at the front desk — warm, relaxed, spontaneous, present. This is a real-time conversation, NOT an IVR and NOT a script: there are NO menus, NO "press 1", NO fixed sequence of questions. Follow the caller's lead, let them speak freely, and respond to exactly what they actually said — in any order they bring things up. React like a person: acknowledge feelings briefly ("അയ്യോ", "sorry to hear that"), and a little natural small-talk is fine when it relates to their visit, health, or the hospital. Keep replies short and quick so it feels live — one thought at a time, never a monologue or a read-out list. You have real freedom in HOW you talk; the ONLY hard boundary is the TOPIC: stay within this hospital and general medical/health matters (see SCOPE). For anything genuinely outside that, warmly redirect — never refuse roboticly.

CONNECT TO A PERSON: If the caller asks to talk to a human, a real person, staff, the receptionist, the manager, or to be put through to someone, call connect_to_human — never refuse this and never say you cannot transfer.

SCOPE — STAY ON TOPIC: You ONLY help with {hosp_name} and its services: appointments, doctors, departments, timings, open/closed status, the hospital's own address, fees, reports, lab/scan, billing, and medical or emergency assistance. You are NOT a general assistant. Questions about THIS hospital's own doctors, departments, availability, timings, fees or services are ALWAYS in scope — e.g. "which doctors are there?", "who is in General Medicine?", "ജനറൽ മെഡിസിനിൽ ആരൊക്കെ ഉണ്ട്?", "is Dr. X free?". NEVER decline these as off-topic; ANSWER them (use check_department_availability to list ONLY the doctors who have open slots, with their times). CRITICAL: ANY health issue the caller mentions — a symptom, pain, injury, fracture, broken bone, fever, illness, or "something is wrong with my <body part>" — is ALWAYS in scope. NEVER decline or treat it as off-topic; instead apply SYMPTOM ROUTING below to name the right department and offer to book an appointment (or route to Emergency if urgent). Only decline things genuinely unrelated to healthcare or this hospital. If the caller asks about anything unrelated to this hospital — bus/train/KSRTC routes or numbers, traffic, weather, news, general knowledge, sports, other businesses or hospitals, online maps, or any off-topic chit-chat — do NOT answer it and do NOT make up an answer. Use the off-topic decline ONLY when you CLEARLY understood the request and it is genuinely unrelated to this hospital or to health — if you are NOT sure what the caller said, do NOT decline; treat it as a misheard hospital/medical term and ask “Did you mean X?” with the closest match instead. When you do decline, use ONE short sentence in the caller's own language and steer back to the hospital, e.g. Malayalam "ക്ഷമിക്കണം, എനിക്ക് {hosp_name}-മായി ബന്ധപ്പെട്ട കാര്യങ്ങളിൽ മാത്രമേ സഹായിക്കാൻ കഴിയൂ. എന്താണ് വേണ്ടത്?" / English "Sorry, I can only help with matters related to {hosp_name}. How can I help you here?". To reach the hospital you may give ONLY its address and nearby landmarks from the HOSPITAL section — NEVER invent bus numbers, routes, schedules, or directions that are not listed there.

LANGUAGE: Speak Malayalam (Malayalam script) by default. If the caller clearly speaks English, reply in English; if they speak Manglish (Malayalam in Latin script), match their Manglish. Detect the caller's language from their FIRST message and LOCK to it for the ENTIRE call. Do NOT switch for stray foreign words, names, brand names, or medical terms. ONLY switch language when the caller EXPLICITLY asks for it — e.g. "switch to English", "can you speak in Hindi", "speak Malayalam". A word or two of another language is NEVER a reason to switch; keep replying in the locked language. Keep replies SHORT — ONE short sentence when possible (two at most), ending with ONE question only when you need something. Don't over-explain, list everything, repeat the caller, or add notes they didn't ask for (e.g. unsolicited "if it's an emergency..." reminders — only mention emergencies if the caller raises one). Short replies also reach the caller faster. Speak plainly and naturally, like a real receptionist. NATIVE MALAYALAM: when speaking Malayalam, use everyday SPOKEN Malayalam like a warm local Thrissur receptionist — natural, colloquial, conversational. Use common spoken words and natural particles (ട്ടോ, അല്ലേ, ഒക്കെ, ണ്ട്), light contractions and plain vocabulary. AVOID stiff, formal, literary or Sanskritised Malayalam and word-for-word English translations. Keep English only for proper nouns, doctor names and medical terms people normally say in English.

SCRIPT: Keep these terms in English (Latin script) exactly as Keralites say them — do NOT transliterate: doctor, appointment, OPD, token, lab, scan, report, casualty, emergency, timing, consultation, booking. Bulbul TTS pronounces Latin letters in English automatically; transliterating them breaks pronunciation.

TIME IN MALAYALAM: When you say a clock time in Malayalam, speak it in natural spoken Malayalam WORDS, never as digits (do NOT say "12:45" — Bulbul would read it in English). Format: [part of day] + [hour in Malayalam] മണി + [minutes in Malayalam] (drop the minutes for an exact hour). Hours: 1 ഒരു, 2 രണ്ട്, 3 മൂന്ന്, 4 നാല്, 5 അഞ്ച്, 6 ആറ്, 7 ഏഴ്, 8 എട്ട്, 9 ഒമ്പത്, 10 പത്ത്, 11 പതിനൊന്ന്, 12 പന്ത്രണ്ട്. Common minutes: 15 പതിനഞ്ച്, 20 ഇരുപത്, 30 മുപ്പത്, 45 നാല്പത്തിയഞ്ച്. Part of day: രാവിലെ (morning, before noon), ഉച്ച കഴിഞ്ഞ് (afternoon), വൈകിട്ട് (evening), രാത്രി (night). Examples: 9:00 AM → "രാവിലെ ഒമ്പത് മണി"; 9:30 AM → "രാവിലെ ഒമ്പത് മണി മുപ്പത്"; 12:45 PM → "ഉച്ച കഴിഞ്ഞ് പന്ത്രണ്ട് മണി നാല്പത്തിയഞ്ച്"; 5:15 PM → "വൈകിട്ട് അഞ്ച് മണി പതിനഞ്ച്"; 10:00 PM → "രാത്രി പത്ത് മണി". In English or Manglish, a normal "12:45 PM" is fine.

MALAYALAM STYLE: Speak like a warm, friendly Malayali receptionist — everyday spoken Malayalam (സംസാരഭാഷ), simple and natural, never literary or Sanskritic. Use natural everyday words: "എന്താണ് വേണ്ടത്?" not "എന്ത് ആവശ്യമാണ്?"; "ഒന്ന് പറയാമോ?" not "ദയവായി അറിയിക്കുക"; "ശരി" / "ഉറപ്പായി" for okay. Sound caring and unhurried, like a real person — not a robotic translation.
TENSES — use natural everyday forms, no gender suffix: present "-ുന്നു" (ഞാൻ ബുക്ക് ചെയ്യുന്നു), past "-ി/-ു" (ബുക്ക് ചെയ്തു, വന്നു — never "വന്നാൾ"), future "-ും" (ബുക്ക് ചെയ്യും).
SUFFIX SANDHI — when a word ending in the chillu "ൽ" takes a case suffix, the "ൽ" becomes "ലിന്" (never write "ൽ" + suffix directly): കാൽ → കാലിന്, മുക്കാൽ → മുക്കാലിന്, ഹോസ്പിറ്റൽ → ഹോസ്പിറ്റലിന്.
TIME — speak the clock naturally and NEVER say the unit words "മണിക്കൂർ" (hour) or "മിനിറ്റ്" (minute). Use these forms: :00 (o'clock) = number + മണി ("ഒരു മണി" 1:00, "പത്ത് മണി" 10:00); :15 = "Xഏ കാൽ" ("10ഏ കാൽ" 10:15); :30 (half) = the "-ര" form ("ഒന്നര" 1:30, "രണ്ടര" 2:30, "പത്തര" 10:30); :45 = "Xഏ മുക്കാൽ" ("10ഏ മുക്കാൽ" 10:45); any other minute Y = "Xഏ Y" ("10ഏ 20" 10:20). Always add the part of day ("രാവിലെ 10 മണി", "ഉച്ചയ്ക്ക് 2 മണി", "വൈകുന്നേരം 5ഏ 20"). Never say "10 AM", "10:00", or "10 മണിക്കൂർ 30 മിനിറ്റ്".
DATES (Malayalam) — understand these from the caller AND use them when speaking dates back: ഇന്ന് (today), നാളെ (tomorrow), മറ്റന്നാൾ (day after tomorrow), ഇന്നലെ (yesterday), ഈ ആഴ്ച (this week), അടുത്ത ആഴ്ച (next week), ഈ മാസം (this month), അടുത്ത മാസം (next month). Weekdays: തിങ്കളാഴ്ച=Mon, ചൊവ്വാഴ്ച=Tue, ബുധനാഴ്ച=Wed, വ്യാഴാഴ്ച=Thu, വെള്ളിയാഴ്ച=Fri, ശനിയാഴ്ച=Sat, ഞായറാഴ്ച=Sun; "next Monday" = അടുത്ത തിങ്കളാഴ്ച. From TODAY's date (below) compute the absolute date and pass it to tools as YYYY-MM-DD, but SPEAK it back the natural Malayalam way — ഇന്ന്/നാളെ/മറ്റന്നാൾ for the next few days, otherwise the weekday like "വ്യാഴാഴ്ച". NEVER read the raw "2026-06-21" or English month names to the caller.
For Manglish callers (Malayalam in Latin script), reply in Manglish matching their mix.

{grammar_block}
ONE QUESTION AT A TIME: Ask for only ONE missing piece per turn — never bundle questions. For booking you eventually need: department, who it's for, name, age, gender — then OFFER an available slot (do NOT ask the patient for a time). Collect them ONE per turn, but follow the caller's order naturally rather than forcing this sequence. Wait for each answer before asking the next. Do NOT preface a question with an acknowledgement that echoes the previous answer (no "ok, 17 years…" / "ശരി, പതിനേഴ്…"); just ask the next question. Confirm everything only ONCE, at the very end before booking.

SYMPTOM ROUTING: If the caller describes a symptom or problem instead of naming a department or doctor, map it to the right specialty, suggest it in ONE short sentence, and offer to book. Use ONLY specialties listed in the HOSPITAL section. Common mappings: chest pain / palpitations / breathlessness → Cardiology; fever / cough / cold / body pain / general illness → General Medicine; headache / dizziness / numbness / fits / stroke → Neurology; bone / joint / fracture / back / knee pain → Orthopaedics; a child or baby → Paediatrics; ear / nose / throat / hearing → ENT; skin / rash / hair → Dermatology; pregnancy / periods / women's health → Obstetrics & Gynaecology; eye / vision → Ophthalmology; tooth / gum → Dental; stomach / acidity / digestion / liver → Gastroenterology; kidney / urine → Nephrology or Urology. If the matching specialty isn't listed, say so and offer the closest one or General Medicine. If symptoms sound urgent or life-threatening (severe chest pain, unconsciousness, heavy bleeding, breathing difficulty), tell them to come to Emergency / Casualty immediately. Confirm the specialty with the caller before booking.

ANSWER QUESTIONS FIRST: If the caller asks a question at any point — especially "which doctors are there?" / "ആരൊക്കെ ഉണ്ട്?" / "ഏതൊക്കെ ഡോക്ടർമാർ ഉണ്ട്?" — ANSWER it right away before anything else. For a doctor list, call check_department_availability and name ONLY the doctors who have open slots (with their times); the tool automatically checks the next available days if today is full. Say ONLY the doctor NAMES first and ask which doctor; give that doctor's available time(s) ONLY AFTER the caller picks one (then call check_availability). NEVER list doctors who have no open slots. NEVER ignore their question and re-ask your own (e.g. "shall I book?" / "confirm the department"). DO NOT LOOP: re-ask a question at most ONCE. If the caller's reply still does not answer it the second time (garbled, unclear, or unrelated — common on a weak line), do NOT ask the same thing a third time. Instead either OFFER concrete choices to pick from (e.g. propose a specific available slot, or two doctor names), or say you're having trouble hearing clearly and offer to note a callback. NEVER repeat an identical question three or more times. If the caller asked something else, handle that first, then resume.

BOOKING — the steps below are a CHECKLIST of what you eventually need, NOT a rigid script: gather these naturally as the conversation flows, in whatever order they come up, and skip anything the caller already told you. Still ask only ONE thing per reply; never bundle several questions; never re-ask something already answered.
1. FIRST ask which department/specialty (skip if already named). If the caller already named a department, symptom, or doctor — SKIP this step and IMMEDIATELY call check_department_availability or check_availability. NEVER ask "do you want to book?" or "shall I check?" — just call the tool directly.
2. Department NOT in the HOSPITAL section → apologise in ONE sentence, then end_call. No transfer, no alternatives.
3. Department exists → call check_department_availability for that department. The tool automatically finds the next available day if today has no slots left. Tell the caller the available doctors and their open times. If the caller is not comfortable with the offered time, ask what time they prefer and check if that slot is available. Only say appointments are unavailable if the tool explicitly returns NO_SLOTS for the entire week.
4. Ask WHO it's for (yourself or someone else) — ask this ONCE only. Once you know (e.g. "my younger sister"), NEVER ask it again.
5. Collect the patient's details ONE PER REPLY, asking only the next missing one: name → age → gender. AGE: a patient can be ANY age — years, months, weeks, or days. Use EXACTLY the unit the caller gives (e.g. “3 months”, “2 weeks”, “40 years”) and say it back in that SAME unit; a bare number with no unit means YEARS. NEVER force years, never change the unit, and never invent a unit (do not turn a plain “17” into “17 days”). Apart from that, don't echo the age — just move on. Gender must be male/female/other (ആൺ/പെൺ); if the answer isn't a valid gender, briefly re-ask once. As soon as the caller gives a name — even a single word like "Pranav" — ACCEPT it as the name and move straight to the next question; never re-ask for the name once you have one. As soon as you have the name (and age/gender if given), SILENTLY call remember_patient with them so the call is on record even if no booking completes.
6. Do NOT ask the patient for a date or time. Call check_availability (it automatically finds the next available day if today is full). OFFER the earliest open slot directly — e.g. "ഡോ. X-ന് നാളെ രാവിലെ 9:30-ന് ഫ്രീ ഉണ്ട്, അത് ശരിയാണോ?". If the caller says the time does not suit them, ask what time they would prefer and check that specific slot. NEVER ask them to name an exact time first — always offer one proactively.
7. Read the full details (patient name, age, doctor, date, time) back EXACTLY ONCE — only at this final step, never earlier and never again. Get a single "yes", then call book_appointment (pass patient_age, patient_gender, booked_for = "self" or the relation). Do NOT re-state or re-confirm details the caller already gave on earlier turns; once they say yes, book and move on.

PUNCTUATION: Always write replies with proper punctuation — full stops, commas and question marks — so the spoken voice has natural pauses and intonation, and numbers, dates and booking codes are read clearly. NAME COLLECTION: Ask warmly, not robotically — Malayalam/Manglish → "ഒന്ന് പേര് പറഞ്ഞോ?"; English → "Could I get your name?" NAME CORRECTION: If the caller says the name you used is wrong, or corrects it (e.g. "it's Nihal, not Nikhil"), apologise briefly and ask them to SPELL it out letter by letter, then read the spelled-out name back once to confirm before using it.

UNCLEAR / WEAK LINE: Phone audio can break up. If a reply is empty, garbled, a stray fragment, or doesn't fit what you asked, do NOT guess the answer and do NOT move on. If the unclear word SOUNDS LIKE a hospital or medical term, pick the CLOSEST matching department, doctor, service, symptom, or common word you know for THIS hospital and ask “... ആണോ ഉദ്ദേശിച്ചത്?” / “Did you mean X?” so the caller can confirm or correct. Only if you truly cannot guess any close match, say ONE short “ക്ഷമിക്കണം, ഒന്നുകൂടി പറയാമോ?” and re-ask the SAME single thing. NEVER use the off-topic / “I can only help with hospital matters” reply for a word that is merely unclear, garbled, or unrecognized. ONLY if a name, age, date or time sounds garbled or you are genuinely unsure, ASK "... X ആണോ?" / "did you mean X?" with your single best guess and let the caller confirm or correct it — for that ONE item only. Never confirm or repeat back values that were clear. If it was clear, do NOT echo or acknowledge it — go straight to the next question. Never invent or "auto-correct" a name into something the caller didn't say. SIDE CONVERSATION: If the caller is clearly talking to someone ELSE nearby or having a side / background conversation that is NOT directed at you (addressing a third person, "ഒരു മിനിറ്റ്" / "wait" / "one minute", or unrelated chatter that is not an answer to your question or a request to you), do NOT respond to that content and do NOT advance the booking. Stay quiet; only if it continues, say ONE brief "സാരമില്ല, ഞാൻ കാത്തിരിക്കാം — തയ്യാറാകുമ്പോൾ പറഞ്ഞോളൂ" (no problem, I will wait) and then gently re-ask your last question. Act ONLY on what is actually said TO you.

CONTEXT MEMORY: Your full conversation history is visible — use it actively. Never re-ask for something the caller already said this call: their name, doctor preference, date, symptoms, reason. Reference what they told you: "ഡോ. രാജൻ — Monday 10 AM ആണോ?" or "You mentioned Dr. Rajan — confirming Monday at 10?" If a caller corrects you, update and confirm the new value. Never say "I don't have that information" about something they said earlier in this same call. NEVER claim you have already told or given the caller something (doctor names, available times, an answer) unless you ACTUALLY said it in an earlier turn — if you have not yet, just provide it now via the right tool. Keep the conversation FLOWING and continuous — briefly acknowledge what the caller just said (ശരി / മനസ്സിലായി) and build on the previous turns so it feels like one natural chat, never disconnected one-off answers.

DOCTOR NAME USE: Say the chosen doctor's name ONCE when the doctor is decided (and once in the final booking recap). Do NOT repeat the doctor's name in every following reply — refer to "ഡോക്ടർ" / "the doctor". Say the full name again ONLY if the caller asks who the doctor is. NAME USE: Say the hospital's name ONCE in the opening greeting. After that do NOT repeat the hospital name in every reply — it sounds robotic. Refer to it naturally as "ഇവിടെ" (here) or "ഞങ്ങൾ/ഞങ്ങളുടെ" (we/our), and only say the full name again if the caller asks who they've reached or it's truly needed to avoid confusion.

PUNCTUATION: Use full, natural punctuation — commas for pauses, a full stop to end, a question mark on questions — the voice uses it for intonation. Do NOT repeat the same word back-to-back, and do NOT echo the caller's exact words; rephrase.

ANSWER INSTANTLY from the HOSPITAL section below — NO tool, NO "let me check" — for: whether a department exists, its floor/location, operating hours, open/closed, doctor names and their department, emergency numbers, address, phone, and anything in the HANDBOOK. You already know these; just say the answer.

USE A TOOL for live data or write actions, and call it DIRECTLY without announcing it. Do NOT send a separate "ഒരു നിമിഷം" / "one moment" reply and then wait — that ends your turn, so if the caller says anything next the tool call is dropped and never runs. A soft typing sound already covers the brief lookup. Just call the tool and then speak the result in the same flow. Confirm the full details (name, doctor, date, time) together only ONCE, immediately before calling book_appointment — NEVER re-confirm after each individual answer and NEVER repeat the confirmation on every turn. AVAILABILITY: if a tool reports a department or doctor is not found / not listed / has no slots, do NOT guess or invent one — APOLOGISE and clearly tell the caller we do not have that department or doctor (e.g. Malayalam "ക്ഷമിക്കണം, ഞങ്ങൾക്ക് ആ ഡിപ്പാർട്ട്മെന്റ് / ഡോക്ടർ ഇല്ല" / English "Sorry, we don't have that department / doctor"), then offer a related department we DO have or ask what else they need.

DATE & TIME: Silently convert the caller's words to an absolute date and 24-hour time before calling a tool. Use TODAY (below) as the reference: "tomorrow" = today + 1, "day after" = today + 2, a weekday name = its next occurrence. Pass date as YYYY-MM-DD and time as HH:MM (e.g. "10 in the morning" → 10:00, "3 pm" → 15:00). AM/PM IS CRITICAL: if the caller gives a time WITHOUT saying morning/afternoon/evening or AM/PM (e.g. just "6:30"), do NOT assume — ask "രാവിലെയോ വൈകുന്നേരമോ?" (morning or evening?). Keep the exact minutes (6:30 stays :30, never round to :00). Before booking, read the final time back in the caller's language (e.g. "വൈകുന്നേരം 6:30-ന്, ശരിയാണോ?") and get a yes. NEVER speak the current clock time back. If you can't tell the day or time, ask in one short question — never guess. NEVER book or reschedule for a PAST date/time (e.g. "yesterday"/ഇന്നലെ) — politely say that day has passed and ask for an upcoming day. (Asking ABOUT a past or existing appointment is fine — look it up normally.)

ENDING THE CALL: When the caller signals they are finished — "ok thanks", "that's all", "no, nothing else", "goodbye" — do NOT ask another question and do NOT re-offer help. Say ONE short farewell and call end_call. Only keep the conversation going if they actually raise a new request. NEVER loop: do not repeat the same question, the same information, or "anything else?" over and over. Once the caller's request is handled and they have nothing more, give one short farewell and call end_call so the call hangs up automatically — do not wait in silence.

NEXT-AVAILABLE DOCTOR: When the caller asks for any available doctor / a department/specialty (e.g. "a cardiologist", "whichever doctor is free soonest") rather than a named doctor — AND that department/specialty is listed in the HOSPITAL section — NEVER repeat their request back as a question and NEVER make them choose. Pick ONE doctor in THAT SAME department, call check_availability, and STATE the soonest open slot directly ("Dr. X is free tomorrow at 10:00 — shall I book that?"). Only offer another doctor if that one has no slots or the caller declines.

MATCH THE EXACT DEPARTMENT/DOCTOR ASKED: Answer ONLY about the specific department, specialty, or doctor the caller named. NEVER substitute a different one — if they ask about Neurology, do NOT answer with Orthopedics; if they ask for Dr. A, do NOT offer Dr. B unless they ask. First decide whether what they named actually appears in the HOSPITAL section:
 • IF IT IS LISTED: never say it is unavailable or does not exist. If they named a specialty (e.g. "a cardiology doctor"), pick a doctor from THAT department and proceed — do NOT reply "no doctor available". If they don't know a name, briefly list that department's own doctors and ask which one. Only after check_availability returns zero slots may you say that specific doctor has no slots that day.
 • IF IT IS NOT LISTED: do NOT invent it and do NOT swap in a different department or doctor as if it were the one asked. Apologise plainly in ONE short sentence that the hospital does not have that department/specialty/doctor here, then call end_call.

NEVER invent doctor names, departments, specialties, timings, fees, or availability — if it is neither in the HOSPITAL section nor a tool result, do not make it up; say it is not available here and transfer.

CRITICAL: Your spoken reply is plain natural language ONLY. NEVER write code, JSON, or function/tool syntax (no "<function=...>", no "{...}"). NEVER use markdown or formatting of any kind — no asterisks (*), no **bold**, no bullet points, no hyphen/dash lists, no headings, no emojis, no line-by-line lists. This is a PHONE CALL: such symbols are read aloud or break the voice. Give floor/extension/department details only if asked, woven into one plain spoken sentence (e.g. "കാർഡിയോളജി രണ്ടാം നിലയിലുണ്ട്"), never as a list. NEVER announce or narrate tool use — do NOT say "I am calling a function", "let me check", "fetching details", "one moment" or anything similar. Speak ONLY the final answer.

If a [SENSORY:...] tag shows TENSION=TREMBLING or VOL/PITCH=LOW → the caller may be in pain or frightened: speak gently, reassure first.

EMERGENCY (chest pain, severe bleeding, unconscious, can't breathe, stroke, poisoning): call alert_emergency FIRST, say "Connecting you to emergency — please stay on the line."

DIGITS: 1=OPD/doctor 2=emergency 3=lab 4=pharmacy 5=billing 0=reception *=repeat

AFTER HOURS: if CLOSED, give next opening and offer (a) book for then, (b) callback, or (c) emergency. Never say "closed, goodbye".

EXISTING APPOINTMENT: you do NOT know the caller in advance and have NO record of their past calls or bookings. If they ask about an appointment they ALREADY have — when it is, which doctor or department, or whether it is confirmed — call get_my_appointments (it looks them up by their phone number) and answer ONLY from what it returns. Never guess or claim to remember them. If it returns nothing for their number, say there is nothing on record and offer to book.
{outbound_block}
HOSPITAL:
{hosp_block}

TODAY: {day_name} {date_iso}, {time_str} IST | STATUS: {open_status}
The date above is the real current date — compute every appointment date from it (tomorrow = this date + 1 day, etc.) and always pass dates to tools as YYYY-MM-DD."""


def _build_greeting(hospital_ctx, outbound_context: Optional[dict],
                    returning_name: str = "", agent_language: str = "ml-IN") -> str:
    # Fixed text per call type. Inbound uses a time-of-day Malayalam greeting so the
    # audio is identical per hour-bucket → first call of each bucket warms the TTS
    # cache, every subsequent call is an instant cache hit.
    from src.tts_normalize import name_for_lang
    # Hospital name: Latin form for outbound (a proper noun spoken inside the
    # greeting, even a Malayalam one). Inbound uses the native-script name so the
    # TTS phonetics are correct.
    if hospital_ctx:
        hosp_name_en = hospital_ctx.name
        hosp_name = (hosp_name_en if outbound_context
                     else name_for_lang(hospital_ctx.name, hospital_ctx.name_ml or "", agent_language))
    else:
        hosp_name = hosp_name_en = "the hospital"

    if outbound_context:
        call_type = outbound_context.get("call_type", "")
        pname = outbound_context.get("patient_name", "")
        dname = outbound_context.get("doctor_name", "")
        # Templates add the title themselves, so strip a leading "Dr." from the
        # stored name — otherwise it reads "Dr. Dr. Meera Joseph".
        _d = dname.strip()
        if _d.lower().startswith("dr."):
            _d = _d[3:].strip()
        elif _d.lower().startswith("dr "):
            _d = _d[3:].strip()
        dname = _d
        date  = outbound_context.get("appointment_date", "")
        ttime = outbound_context.get("appointment_time", "")
        # Malayalam-first: a ml-IN tenant greets outbound in Malayalam (medical and
        # proper nouns stay English, the way Keralites actually speak). Other
        # languages fall back to English.
        ml = (agent_language or "").lower().startswith("ml")
        # Localize date/time so the greeting speaks naturally ("നാളെ", "ജൂൺ 28-ന്",
        # "tomorrow") instead of the raw stored value — a real reminder carries an
        # ISO date like "2026-06-28" and a "14:30" time, which used to be read out
        # verbatim. Falls back to the raw value if it can't be parsed.
        from src.telephony.livekit_tools import _spoken_appt_date, _spoken_time_en, _ml_time
        _date_ml = _spoken_appt_date(date, True)
        _date_en = _spoken_appt_date(date, False)
        _time_ml = _ml_time(ttime) if ttime else ""
        _time_en = _spoken_time_en(ttime) if ttime else ""
        if call_type == "confirmation":
            if ml:
                _at = f"{_time_ml}-ന് " if _time_ml else ""
                return (
                    f"നമസ്കാരം {pname}, {hosp_name}-ൽ നിന്നാണ് വിളിക്കുന്നത്. "
                    f"{_date_ml} {_at}{dname} ഡോക്ടറുമായുള്ള നിങ്ങളുടെ appointment "
                    f"ഉറപ്പിക്കാനാണ് വിളിച്ചത്. വരാൻ പറ്റുമോ?"
                )
            _aten = f" at {_time_en}" if _time_en else ""
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"I'm calling to confirm your appointment with Dr. {dname} on {_date_en}{_aten}. "
                "Can you attend?"
            )
        elif call_type == "reminder":
            if ml:
                return (
                    f"നമസ്കാരം {pname}, {hosp_name}-ൽ നിന്നാണ്. "
                    f"{_date_ml} {dname} ഡോക്ടറുമായുള്ള appointment ഓർമിപ്പിക്കാനാണ് വിളിച്ചത്. "
                    f"എന്തെങ്കിലും സംശയം ഉണ്ടോ?"
                )
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"This is a reminder of your appointment with Dr. {dname} on {_date_en}. "
                "Do you have any questions?"
            )
        elif call_type == "doctor_availability":
            dstatus = outbound_context.get("doctor_status", "")
            if dstatus == "unavailable":
                if ml:
                    return (
                        f"നമസ്കാരം {pname}, ഇന്നത്തെ നിങ്ങളുടെ appointment സംബന്ധിച്ച് "
                        f"{hosp_name}-ൽ നിന്നാണ്. ക്ഷമിക്കണം, {dname} ഡോക്ടർ ഇന്ന് ലഭ്യമല്ല. "
                        f"reschedule ചെയ്യാൻ സഹായിക്കട്ടെ?"
                    )
                return (
                    f"Hello {pname}, this is {hosp_name} calling about your appointment today. "
                    f"Unfortunately Dr. {dname} is unavailable today. May I help you reschedule?"
                )
            elif dstatus == "delayed":
                if ml:
                    return (
                        f"നമസ്കാരം {pname}, ഇന്നത്തെ appointment സംബന്ധിച്ച് {hosp_name}-ൽ നിന്നാണ്. "
                        f"{dname} ഡോക്ടർക്ക് ഇന്ന് അൽപം വൈകും. നിങ്ങൾ വരാൻ ആഗ്രഹിക്കുന്നുണ്ടോ?"
                    )
                return (
                    f"Hello {pname}, this is {hosp_name} calling about your appointment today. "
                    f"Dr. {dname} is running a little late today. Would you still like to come in?"
                )
            if ml:
                return (
                    f"നമസ്കാരം {pname}, ഇന്നത്തെ appointment സംബന്ധിച്ച് {hosp_name}-ൽ നിന്നാണ്. "
                    f"{dname} ഡോക്ടർ planned പ്രകാരം ലഭ്യമാണ്. എന്തെങ്കിലും സംശയം ഉണ്ടോ?"
                )
            return (
                f"Hello {pname}, this is {hosp_name} calling about your appointment today. "
                f"Dr. {dname} is available as scheduled. Do you have any questions?"
            )
        elif call_type == "callback":
            if ml:
                return f"നമസ്കാരം {pname}, {hosp_name}-ൽ നിന്നാണ്. എങ്ങനെ സഹായിക്കാം?"
            return f"Hello {pname}, this is {hosp_name} calling. How can I help you today?"
        elif call_type == "followup":
            if ml:
                return (
                    f"നമസ്കാരം {pname}, {hosp_name}-ൽ നിന്നാണ്. "
                    f"{dname} ഡോക്ടറെ കണ്ടതിനു ശേഷം ഇപ്പോൾ എങ്ങനെ ഉണ്ട്?"
                )
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"How are you feeling after your visit with Dr. {dname}?"
            )

    # Inbound: a custom per-hospital greeting (set in the dashboard) wins; else
    # the language-appropriate time-of-day default.
    _custom_greet = (getattr(hospital_ctx, "greeting", "") or "").strip() if hospital_ctx else ""
    if _custom_greet:
        return _custom_greet
    import pytz as _pytz
    _IST = _pytz.timezone("Asia/Kolkata")
    hour = datetime.now(_IST).hour
    from src.ai.groq_brain import build_greeting_text
    return build_greeting_text(hosp_name, hour, lang=agent_language)


# ==============================================================================
# Agent class — one per call session
# ==============================================================================

class HospitalVoiceAgent(Agent):
    """Arteq hospital voice agent — wraps the full pipeline per call."""

    def __init__(
        self,
        system_prompt: str,
        greeting: str,
        tools: list,
        sensory: AcousticSensoryLayer,
        hospital_id: str,
        caller_phone: str,
        call_id: str,
        hospital_name: str,
        call_started_at: datetime,
        agent_language: str = "ml-IN",
        premium_llm: bool = True,
        vad=None,
    ) -> None:
        # Pin STT to the hospital's language so auto-detect can't flip a Malayalam
        # caller's words into Hindi/Devanagari mid-call (which garbled names).
        # SARVAM_STT_LANGUAGE forces a specific code; "unknown"/unset falls back
        # to the hospital's own agent_language.
        _stt_lang = (os.getenv("SARVAM_STT_LANGUAGE", "") or "").strip()
        if not _stt_lang or _stt_lang == "unknown":
            _stt_lang = agent_language or "unknown"
        super().__init__(
            instructions=system_prompt,
            tools=tools,
            # Saarika = Speech-to-Text (transcribes in the caller's OWN language and
            # native script). Saaras = Speech-to-Text-TRANSLATE (rewrites everything
            # to English) — that silently breaks this agent, because both the reply-
            # language rules in the system prompt and _detect_tts_lang() decide the
            # caller's language from the SCRIPT of the transcript. An English-only
            # transcript makes Arya answer in English even to a Malayalam caller.
            # Model + language are env-overridable so the version can be bumped or the
            # language pinned (e.g. "ml-IN") without a code change. "unknown" lets one
            # agent auto-detect every caller language; pin it if auto-detect is shaky.
            stt=_build_stt(_stt_lang),
            # Reuse the worker-prewarmed VAD (loaded once in prewarm_fnc) so the
            # Silero model load is off the per-call critical path. Fall back to a
            # fresh load if prewarm was skipped. 0.2s end-of-speech silence →
            # Arya starts replying sooner; still long enough not to cut a caller
            # in a natural mid-sentence pause. activation_threshold=0.5 is the
            # Silero default — phone/SIP audio is 8kHz compressed and scores lower
            # than clean mic audio, so raising this threshold blocks real speech
            # from reaching STT. min_speech_duration=0.1 filters sub-100ms noise
            # bursts without affecting speech detection.
            vad=vad or silero.VAD.load(
                min_silence_duration=_VAD_MIN_SILENCE,
                activation_threshold=_VAD_ACTIVATION,
                min_speech_duration=_VAD_MIN_SPEECH,
            ),
            llm=_build_llm(premium=premium_llm),
            tts=_build_tts(agent_language),
        )
        self._greeting = greeting
        self._sensory = sensory
        self._hospital_id = hospital_id
        self._caller_phone = caller_phone
        self._call_id = call_id
        self._hospital_name = hospital_name
        self._call_started_at = call_started_at
        self._agent_language = agent_language

    async def on_enter(self) -> None:
        """Speak the opening greeting the instant the call connects.

        session.say() sends the fixed greeting straight to TTS (no LLM round-trip).
        The greeting text is constant per call, so after the first call its audio
        is served from the TTS cache — the caller hears it with no synth delay. The
        entrypoint also pre-warms that cache concurrently with session start, so
        even the first call on a cold worker is near-instant.
        """
        await self.session.say(self._greeting, allow_interruptions=True)
        # Mark that the opening greeting actually reached TTS. A turns=0 call
        # where this is True is a caller who heard Arya and hung up; if it is
        # False the caller dropped during the pre-greeting setup (dead air).
        try:
            self.session.userdata["greeting_delivered"] = True
            self.session.userdata["greeting_at"] = __import__("time").time()
        except Exception:
            pass

    async def on_user_turn_completed(
        self,
        turn_ctx: agents_llm.ChatContext,
        new_message: agents_llm.ChatMessage,
    ) -> None:
        """
        Intercept each user turn for:
          1. DTMF digit → synthetic phrase
          2. Acoustic metadata injection
          3. Context window pruning
        """
        # Extract plain text from content (ChatContent = str | ImageContent | AudioContent)
        text = ""
        try:
            content = new_message.content
            if isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, str):
                        text += chunk
            elif isinstance(content, str):
                text = content
        except Exception:
            text = ""

        stripped = text.strip()
        _log.info(f"uotc_in txt={stripped[:60]!r} n={len(stripped)} "
                  f"outbound={bool(self.session.userdata.get('is_outbound'))}")

        # Drop sub-threshold transcripts. VAD's min_speech_duration=0.3s already
        # filters most noise bursts, but occasional artefacts still produce a
        # 1-character STT output (a stray vowel, a click). A real utterance is
        # always ≥2 chars. Skip silently — no LLM call, no reply.
        # Only drop 1-char ASCII noise (stray letter/click/punctuation). Real
        # Malayalam/Indic acknowledgements like "ആ" (yes) or "എ" are a single
        # NON-ASCII char and must get through.
        if stripped and len(stripped) < 2 and stripped not in _DTMF and stripped.isascii():
            _log.info("uotc_stop why=subthreshold")
            raise agents_llm.StopResponse()

        # Post-greeting cooldown: ignore short/ambiguous transcripts in the first
        # few seconds after the greeting — phone line noise and SIP artefacts
        # often trigger a false turn that makes the agent ask "are you there?"
        # before the caller has had time to respond.
        _GREETING_COOLDOWN_S = float(os.getenv("GREETING_COOLDOWN_S", "4.0"))
        _cooldown_drop = False
        try:
            _greet_at = self.session.userdata.get("greeting_at", 0)
            if _greet_at and (__import__("time").time() - _greet_at) < _GREETING_COOLDOWN_S:
                if not stripped or len(stripped) < 5:
                    _cooldown_drop = True
        except Exception:
            pass
        if _cooldown_drop:
            _log.info(f"uotc_stop why=greeting_cooldown n={len(stripped)}")
            raise agents_llm.StopResponse()

        # Voicemail detection (OUTBOUND only): if a carrier/iPhone voicemail picked
        # up instead of a human, end the call so the 3x retry rule tries again —
        # never talk to a machine.
        if stripped and self.session.userdata.get("is_outbound"):
            if any(p in stripped.lower() for p in _VOICEMAIL_PATTERNS):
                ud = self.session.userdata
                ud["voicemail_detected"] = True
                new_message.content = []
                print(f"[arteq] voicemail_detected call={ud.get('call_id','')[:8]}",
                      file=sys.stderr, flush=True)

                async def _vm_hangup(rn=ud.get("room_name", "")):
                    try:
                        from src.services.livekit_sip import delete_room
                        await delete_room(rn)
                    except Exception as _vm_exc:
                        print(f"[arteq] voicemail hangup failed: {_vm_exc}", file=sys.stderr)
                if ud.get("room_name"):
                    asyncio.create_task(_vm_hangup())
                raise agents_llm.StopResponse()

        # Remember the caller's language (script-detected) so the live backchannel
        # murmurs in their language, not always Malayalam. Lock language on first
        # real detection — only update on an explicit language-switch request so
        # a stray "okay" (English) doesn't flip a Malayalam caller to English.
        if stripped and len(stripped) >= 3:  # ignore noise/DTMF
            try:
                detected = _detect_tts_lang(stripped, self._agent_language)
                ud = self.session.userdata
                if not ud.get("lang_locked"):
                    if detected != self._agent_language:
                        ud["caller_lang"] = detected
                        ud["lang_locked"] = True
                    elif len(stripped) > 5:  # enough content to be confident
                        ud["caller_lang"] = detected
                        ud["lang_locked"] = True
                else:
                    # Only update language on an explicit switch request
                    low = stripped.lower()
                    switch_phrases = [
                        "switch to", "can you speak in", "please speak in", "talk in",
                        "speak english", "in english", "english please", "speak in english",
                        "speak malayalam", "malayalam please", "in malayalam",
                        "speak hindi", "hindi please", "in hindi",
                        "speak tamil", "tamil please",
                    ]
                    if any(p in low for p in switch_phrases):
                        ud["caller_lang"] = detected
                        # keep lang_locked = True, just updated target
            except Exception:
                pass

        # Detect repeat-utterance requests and replay last agent utterance
        low_stripped = stripped.lower() if stripped else ""
        last_utterance = self.session.userdata.get("last_agent_utterance", "")

        if (last_utterance and stripped and len(stripped) < 60 and
                any(p in low_stripped for p in _REPEAT_PATTERNS)):
            # Replay last utterance without going to LLM
            _log.info("uotc_stop why=repeat_replay")
            self.session.say(last_utterance, allow_interruptions=True)
            raise agents_llm.StopResponse()  # genuinely skip the LLM turn

        # Fast info path: answer a few fixed, unambiguous infos (visiting hours,
        # location, ambulance, parking) straight from the cached hospital context,
        # skipping the LLM (~1.5s). Guarded so it never fires mid-booking.
        if stripped and len(stripped) < 80:
            _ud = self.session.userdata
            _ctx = _ud.get("hospital_ctx")
            _last = (_ud.get("last_agent_utterance") or "").lower()
            _busy = any(m in _last for m in ("പേര്", "വയസ", "തീയതി", "സമയ", "ആണോ പെണ", "ഡോക്ടറെയാണ", "name", "age", "which day", "what time"))
            _bk = any(b in low_stripped for b in ("appointment", "book", "ബുക്ക", "അപ്പോയിന"))
            _ans = None
            if _ctx is not None and not _busy and not _bk:
                # Language-aware answer picker: Malayalam answer only when the call
                # is in Malayalam, else the English answer.
                _lang = str(_ud.get("caller_lang") or getattr(self, "_agent_language", "") or "ml-IN").lower()
                _is_ml = _lang.startswith("ml")
                def _pick(f):
                    a = (getattr(f, "answer", "") or "").strip()
                    a_ml = (getattr(f, "answer_ml", "") or "").strip()
                    return (a_ml or a) if _is_ml else (a or a_ml)
                # Generic match across ALL dashboard FAQs (question words + tags),
                # not just hardcoded visit/parking.
                _best = None
                _best_score = 0
                for f in (_ctx.faqs or []):
                    ql = (getattr(f, "question", "") or "").lower()
                    qwords = [w for w in ql.replace("?", " ").replace(",", " ").split() if len(w) > 3]
                    score = sum(1 for w in qwords if w in low_stripped)
                    score += sum(2 for t in (getattr(f, "tags", None) or []) if str(t).lower() in low_stripped)
                    if score > _best_score:
                        _best_score = score
                        _best = f
                if _best is not None and _best_score >= 2:
                    _ans = _pick(_best)
                if not _ans and ("ambulance" in low_stripped or "അംബുലന്സ" in low_stripped):
                    for e in _ctx.emergency:
                        _lab = (e.label or "").lower()
                        if "ambulance" in _lab or "casualty" in _lab or "emergency" in _lab:
                            _ans = f"{e.label}: {e.phone}."
                            break
                if not _ans and ("ഡോക്ടര്" not in low_stripped and "doctor" not in low_stripped and
                      any(k in low_stripped for k in ("where is", "address", "location", "how to reach", "directions", "വിലാസം", "എവിടെ", "reach the hospital"))):
                    if getattr(_ctx, "address", ""):
                        _nm = _ctx.name_ml or _ctx.name
                        _ans = f"{_nm} {_ctx.address} എന്ന വിലാസത്തിലാണ്."
            if _ans:
                self.session.say(_ans, allow_interruptions=True)
                _log.info("fast_faq_hit")
                raise agents_llm.StopResponse()

        # DTMF: single digit → remap to natural language phrase
        if stripped in _DTMF:
            try:
                new_message.content = [_DTMF[stripped]]
            except Exception:
                pass
            self._sensory.reset()
            return

        # Inject acoustic metadata when noteworthy, and keep it for the call
        # log (emotional_state) — computed-but-discarded sensory data made
        # distressed calls impossible to find afterwards.
        meta = self._sensory.metadata()
        self._sensory.reset()
        if meta and text:
            try:
                new_message.content = [f"{meta}\n{text}"]
            except Exception:
                pass
            try:
                self.session.userdata.setdefault("sensory_events", []).append(meta)
            except Exception:
                pass

        # Context pruning. truncate() keeps the last N items and re-inserts the
        # system prompt at the front. ChatContext.messages is a METHOD in
        # agents 1.5.x (not a list), and there is no _messages attr — the old
        # turn_ctx.messages / turn_ctx._messages code raised and silently
        # skipped, so context grew unbounded.
        try:
            turn_ctx.truncate(max_items=_MAX_CTX + 1)
        except Exception:
            pass
        _log.info(f"uotc_pass_llm txt={stripped[:60]!r}")

    async def tts_node(self, text, model_settings):
        """Streaming tool-syntax stripper — lowest latency for voice.

        Groq's llama-3.3-70b sometimes emits a tool call as literal text
        (`<function=name>{json}</function>` or a `<tool_call>` block) instead of
        through the API tool channel; spoken aloud it is gibberish.

        Rather than buffer the whole reply (which would delay first audio until
        the LLM finished), we strip incrementally: flush every chunk of clean
        text the instant it is provably outside a tool tag, and only hold back
        the minimal tail that could still be the start of one. TTS therefore
        starts on the first words while the LLM is still generating the rest.

        The full cleaned text is stored in session.userdata["last_agent_utterance"]
        so the repeat-utterance handler in on_user_turn_completed can replay it
        without a new LLM round-trip.
        """
        async def _clean():
            full_text = ""
            buf = ""
            async for chunk in text:
                buf += chunk
                buf = _TOOL_SYNTAX_COMPLETE_RE.sub("", buf)   # drop closed blocks
                emit, buf = _split_safe(buf)                   # hold only tag-prefix tail
                if emit:
                    full_text += emit
                    yield emit
            tail = _strip_tool_syntax(buf)                     # flush any unterminated tail
            if tail:
                full_text += tail
                yield tail
            # Store the full spoken text so repeat-utterance can replay it
            if full_text.strip():
                try:
                    self.session.userdata["last_agent_utterance"] = full_text.strip()
                except Exception:
                    pass

        async for frame in Agent.default.tts_node(self, _clean(), model_settings):
            yield frame


# Complete tool-call blocks (have a closing tag) — safe to remove mid-stream.
_TOOL_SYNTAX_COMPLETE_RE = re.compile(
    r"<function\s*=.*?</function\s*>|<tool_call>.*?</tool_call>",
    re.DOTALL | re.IGNORECASE,
)

# Any tool-call markup including an unterminated tail — used at stream end.
_TOOL_SYNTAX_RE = re.compile(
    r"<function\s*=.*?</function\s*>"
    r"|<tool_call>.*?</tool_call>"
    r"|<function\s*=.*$"
    r"|<tool_call>.*$",
    re.DOTALL | re.IGNORECASE,
)

_TOOL_OPENERS = ("<function", "<tool_call")


def _split_safe(buf: str) -> tuple[str, str]:
    """Split into (emit_now, hold). Hold from the first '<' that could begin a
    tool opener — everything before it is provably speakable."""
    for i, ch in enumerate(buf):
        if ch == "<":
            tail = buf[i:].lower()
            if any(op.startswith(tail) or tail.startswith(op) for op in _TOOL_OPENERS):
                return buf[:i], buf[i:]
    return buf, ""


def _strip_tool_syntax(text: str) -> str:
    return _TOOL_SYNTAX_RE.sub("", text).strip()


def _spoken_chars(transcript: list[dict]) -> int:
    """Characters Arya actually spoke (what Sarvam TTS bills on)."""
    return sum(len(m.get("text", "")) for m in transcript if m.get("role") == "assistant")


def _cost_breakdown_paise(
    duration_s: float,
    transcript: list[dict],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
):
    """Per-service cost from REAL usage (see src.services.cost_model).

    STT on real audio seconds, TTS on real spoken characters, LLM on real
    prompt/completion tokens × the provider's published price. Telephony here is
    the duration-based estimate; the Vobiz CDR job later overwrites it with the
    real billed INR cost. Returns a CostBreakdown (has .cost_paise + .as_dict())."""
    from src.services.cost_model import call_cost_breakdown
    return call_cost_breakdown(
        duration_s=duration_s,
        spoken_chars=_spoken_chars(transcript),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


class _LatencyMeter:
    """Running average of perceived response latency — the time from the caller
    finishing their turn to Arya's first audio. Built from the per-turn metrics
    LiveKit now attaches to each conversation item (`ChatMessage.metrics`):
    end-of-turn delay (VAD settle) + LLM time-to-first-token + TTS
    time-to-first-byte. Each stage is averaged independently (they don't all fire
    on every turn), then summed, so a missing metric on one turn doesn't skew the
    total. Surfaced as latency_avg_ms on the call log to catch a prod latency
    regression we can't otherwise see.

    (Sourced from the `conversation_item_added` event since `metrics_collected`
    was deprecated in livekit-agents 1.5 — the MetricsReport keys map 1:1 to the
    old per-stage metric attributes.)"""

    def __init__(self) -> None:
        self._sum = {"eou": 0.0, "llm": 0.0, "tts": 0.0}
        self._cnt = {"eou": 0, "llm": 0, "tts": 0}
        # Real LLM token usage accumulated across the call, for per-service cost.
        self._prompt_tokens = 0
        self._completion_tokens = 0

    def on_item(self, ev) -> None:
        item = getattr(ev, "item", None)
        metrics = getattr(item, "metrics", None)
        if not isinstance(metrics, dict):
            return
        # MetricsReport keys; map to the old end_of_utterance_delay/ttft/ttfb.
        for mkey, key in (
            ("end_of_turn_delay", "eou"),
            ("llm_node_ttft", "llm"),
            ("tts_node_ttfb", "tts"),
        ):
            val = metrics.get(mkey)
            if isinstance(val, (int, float)) and val > 0:
                self._sum[key] += float(val)
                self._cnt[key] += 1
        # Per-turn latency line (each turn, not just the call average).
        _e = metrics.get("end_of_turn_delay"); _l = metrics.get("llm_node_ttft"); _t = metrics.get("tts_node_ttfb")
        _vals = [v for v in (_e, _l, _t) if isinstance(v, (int, float)) and v > 0]
        if _vals:
            self._turn = getattr(self, "_turn", 0) + 1
            _ms = lambda v: round(v * 1000) if isinstance(v, (int, float)) and v > 0 else 0
            print(f"[arteq] turn_latency #{self._turn} total={round(sum(_vals)*1000)}ms "
                  f"eou={_ms(_e)}ms llm={_ms(_l)}ms tts={_ms(_t)}ms", file=sys.stderr, flush=True)
        # Token usage key names vary across livekit-agents versions, so match
        # defensively on substrings rather than a fixed key (logged once in prod
        # via the cost line so we can confirm the live shape).
        for mkey, val in metrics.items():
            if not isinstance(val, (int, float)) or val <= 0:
                continue
            k = mkey.lower()
            if "token" not in k:
                continue
            if "prompt" in k or "input" in k:
                self._prompt_tokens += int(val)
            elif "completion" in k or "output" in k:
                self._completion_tokens += int(val)

    def tokens(self) -> tuple[int, int]:
        """(prompt_tokens, completion_tokens) accumulated across the call."""
        return self._prompt_tokens, self._completion_tokens

    def avg_ms(self) -> int:
        total_s = sum(
            (self._sum[k] / self._cnt[k]) for k in self._sum if self._cnt[k]
        )
        return round(total_s * 1000)

    def breakdown_ms(self) -> dict:
        """Per-stage average latency in ms — shows WHERE the response delay is:
        eou = end-of-turn / VAD settle, llm = LLM time-to-first-token,
        tts = TTS time-to-first-byte. Sum ≈ avg_ms()."""
        return {
            k: (round((self._sum[k] / self._cnt[k]) * 1000) if self._cnt[k] else 0)
            for k in self._sum
        }



# ==============================================================================
# Agent entrypoint
# ==============================================================================

async def _run_ambient_audio(room: rtc.Room) -> None:
    """Publish low-level background noise as a second audio track for the call.

    The LiveKit SIP gateway mixes all tracks from a participant before sending
    to PSTN, so this ambient sound is heard by the caller throughout. Task
    cancellation is the shutdown signal — finally block unpublishes cleanly.

    AMBIENT_NOISE_LEVEL=0 disables it entirely — no track is published and the
    caller hears a clean line.
    """
    if _AMBIENT_LEVEL <= 0.0:
        return
    source = rtc.AudioSource(_AMBIENT_SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("arteq-ambient", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions())

    buf, n, pos = _AMBIENT_BUF, len(_AMBIENT_BUF), 0
    frame_s = _AMBIENT_FRAME_SAMPLES / _AMBIENT_SAMPLE_RATE
    try:
        while True:
            end = pos + _AMBIENT_FRAME_SAMPLES
            chunk = buf[pos:end] if end <= n else np.concatenate([buf[pos:], buf[: end - n]])
            pos = end % n
            await source.capture_frame(rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=_AMBIENT_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=_AMBIENT_FRAME_SAMPLES,
            ))
            await asyncio.sleep(frame_s)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await room.local_participant.unpublish_track(track.sid)
        except Exception:
            pass


async def _prewarm_static_phrases(lang: str) -> None:
    """Cache TTS audio for deterministic per-language strings that will be spoken.

    LLM replies are too variable to cache. Only hardcoded strings (watchdog
    farewell, fallback error message) reliably match their cache key at runtime.
    """
    from src.ai.groq_brain import _FALLBACK_MESSAGES
    phrases = [
        _WATCHDOG_FAREWELLS.get(lang, _WATCHDOG_FAREWELLS["en-IN"]),
        _FALLBACK_MESSAGES.get(lang, _FALLBACK_MESSAGES["en-IN"]),
    ]
    tts = BulbulV3TTS(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        target_language_code=lang,
        speaker="priya",
        pace=_TTS_PACE,
    )
    for phrase in phrases:
        try:
            stream = tts.synthesize(phrase)
            async for _ in stream:
                pass
            await stream.aclose()
        except Exception:
            pass


async def _prewarm_greeting_audio(text: str, lang: str) -> None:
    """Synthesize the greeting once up front so its audio is in the TTS cache
    before the call's on_enter fires — removes the first-call Bulbul round-trip
    from what the caller hears. Best-effort: any failure just means on_enter
    synthesizes the greeting live, exactly as before."""
    try:
        tts = BulbulV3TTS(
            api_key=os.getenv("SARVAM_API_KEY", ""),
            target_language_code=lang,
            speaker="priya",
            pace=_TTS_PACE,
        )
        stream = tts.synthesize(text)
        try:
            async for _ in stream:
                pass
        finally:
            await stream.aclose()
    except Exception:
        pass


async def entrypoint(ctx: JobContext) -> None:
    """One LiveKit room = one call. Called by the WorkerOptions dispatcher."""
    await ctx.connect()
    room_name = ctx.room.name
    call_id = str(uuid.uuid4())
    call_started_at = datetime.now(timezone.utc)
    _log.info("arteq call room=%s call_id=%s", room_name, call_id[:8])

    # ── Hospital context (tenant-aware: binds to the tenant's own DB) ──────────
    hospital_id, tenant_features, _tenant = await _resolve_call_target(room_name)
    hospital_ctx  = await _load_hospital_ctx(hospital_id)
    hospital_name = hospital_ctx.name if hospital_ctx else "Arteq Hospital"
    hospital_tier = getattr(hospital_ctx, "tier", "hospital") if hospital_ctx else "hospital"

    # ── Outbound context from room metadata ───────────────────────────────────
    outbound_context: Optional[dict] = None
    try:
        if ctx.room.metadata:
            import json as _json
            data = _json.loads(ctx.room.metadata)
            if data.get("call_type"):
                outbound_context = data
    except Exception:
        pass

    # ── Caller phone from participant identity ────────────────────────────────
    caller_phone = ""
    patient_profile: Optional[dict] = None
    try:
        import re as _re_id
        for p in ctx.room.remote_participants.values():
            ident = (p.identity or p.name or "").strip()
            # SIP identities look like "sip_09061409927" / "Phone 09061409927";
            # pull the digits and normalise to E.164 (+91...).
            _digits = _re_id.sub(r"\D", "", ident)
            if len(_digits) >= 10:
                _d = _digits.lstrip("0")
                if len(_d) == 10:
                    caller_phone = "+91" + _d
                elif _d.startswith("91") and len(_d) >= 12:
                    caller_phone = "+" + _d
                else:
                    caller_phone = "+" + _d
                break
        # Cross-call recall disabled by request: do NOT auto-load the caller's
        # name or past appointments and do NOT greet them by name. Patient details
        # are captured fresh each call (remember_patient -> pmeta) and used only to
        # name the recording. A caller can still ask about an existing booking on
        # demand — the LLM calls the get_my_appointments tool, which looks it up by
        # phone at that moment. caller_phone is still resolved above for the
        # recording / call_log row and that on-demand lookup.
        patient_profile = None
    except Exception:
        pass

    # Outbound calls: the dialed participant has not joined yet when the loop above
    # runs (SIP is still ringing) and its identity is only "patient-XXXX", so the
    # parse above leaves caller_phone empty and the call_log records "unknown".
    # Fall back to the dialed number that dial_outbound_vobiz stored in the room
    # metadata so the recipient is attributed correctly.
    if not caller_phone and outbound_context:
        _op = (outbound_context.get("patient_phone")
               or outbound_context.get("phone") or "").strip()
        if _op:
            caller_phone = _op if _op.startswith("+") else "+" + _op.lstrip("+")

    # Stub call_log row up front so the recording is ALWAYS linkable, even if the
    # end-of-call handler is killed (LLM crash / hard job shutdown). The end
    # handler upserts the full transcript + cost over this stub.
    try:
        from src.db.queries import write_call_log as _wcl_stub
        asyncio.create_task(_wcl_stub(
            hospital_id=hospital_id, call_id=call_id, caller=caller_phone or "unknown",
            started_at=call_started_at, ended_at=call_started_at, total_turns=0,
            latency_avg_ms=0, cost_paise=0, transcript=[], intents=[],
            outcome="in_progress", emotional_state="",
            direction="outbound" if outbound_context else "inbound", upsert=False,
        ))
    except Exception:
        pass

    # ── Build system prompt ───────────────────────────────────────────────────
    from src.config.settings import settings
    # Per-hospital language overrides the global env var default. The agent has no
    # spoken name — it identifies only by the hospital — so no agent_name is used.
    agent_language = (getattr(hospital_ctx, "agent_language", None) or settings.AGENT_LANGUAGE)
    system_prompt = _build_prompt(hospital_ctx, outbound_context)
    if patient_profile:
        _appts = patient_profile.get("history") or []
        _lines = []
        for _a in _appts:
            if _a.get("slot"):
                _seg = f"  - {_a['slot']} with Dr. {_a.get('doctor', '?')}"
                if _a.get("dept"):
                    _seg += f" ({_a['dept']})"
                if _a.get("status"):
                    _seg += f" [{_a['status']}]"
                _lines.append(_seg)
        _block = "\n".join(_lines) if _lines else "  (none on record)"
        system_prompt += (
            f"\n\nCALLER ON RECORD: {patient_profile['name']}. Greet them by name.\n"
            f"THEIR APPOINTMENTS (soonest first; dates are YYYY-MM-DD HH:MM):\n{_block}\n"
            "If they ask about their appointment \u2014 when it is, which doctor, which "
            "department, or whether it is confirmed \u2014 answer from this list and speak the "
            "date and time naturally in their language. If none are on record, say so and "
            "offer to book one."
        )

    returning_name = patient_profile["name"] if (patient_profile and not outbound_context) else ""
    greeting = _build_greeting(hospital_ctx, outbound_context, returning_name, agent_language)
    # Start synthesizing the greeting immediately so it is in the TTS cache
    # before on_enter fires. Runs in parallel with all remaining setup work.
    _prewarm_task = asyncio.create_task(_prewarm_greeting_audio(greeting, agent_language))
    # Hold a reference — a bare create_task() can be garbage-collected mid-await,
    # cancelling the prewarm before the static phrases are cached.
    _prewarm_static_task = asyncio.create_task(_prewarm_static_phrases(agent_language))

    # ── Tool set (tier baseline, then per-tenant feature gating) ───────────────
    from src.telephony.livekit_tools import ALL_TOOLS, CLINIC_TOOLS
    from src.tenancy.features import enabled as _feat_on
    tools = list(CLINIC_TOOLS if hospital_tier == "clinic" else ALL_TOOLS)
    def _tool_name(t) -> str:
        return getattr(t, "name", None) or getattr(t, "__name__", "")
    # Department transfer is core IVR-replacement behaviour — every hospital that
    # has set department phone numbers should be able to transfer, not just tenants
    # with the multi_department_routing feature flag. Tool no-ops safely when no
    # number is configured, so it is always safe to expose.
    # (transfer_to_department intentionally left enabled for all tiers)

    # ── Acoustic sensory layer ────────────────────────────────────────────────
    sensory = AcousticSensoryLayer()

    # Per-track audio drain tasks. Tracked so they are cancelled on call end —
    # otherwise each subscribed track leaks a task + AudioStream per call.
    _drain_tasks: list[asyncio.Task] = []

    @ctx.room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        stream = rtc.AudioStream(track)

        async def _drain():
            try:
                async for frame in stream:
                    sensory.feed(frame)
            finally:
                try:
                    await stream.aclose()
                except Exception:
                    pass

        _drain_tasks.append(asyncio.create_task(_drain()))

    # ── Session userdata (accessible inside tools via context.userdata) ───────
    session_data = {
        "hospital_id":         hospital_id,
        "hospital_ctx":        hospital_ctx,
        "hospital_name":       hospital_name,
        "caller_phone":        caller_phone,
        "call_id":             call_id,
        "room_name":           room_name,
        "is_outbound":         bool(outbound_context),
        "transfer_requested":  False,
        "transfer_destination": "",
        "caller_lang":         "ml-IN",
        "intents":             [],   # appended by tools via _mark_intent
        "sensory_events":      [],   # acoustic [SENSORY:...] tags per turn
    }
    # Surface outbound-call context (appointment_id, names, slot) to the tools so
    # confirm_appointment / reschedule act on the RIGHT existing appointment.
    if outbound_context:
        for _k in ("appointment_id", "patient_name", "doctor_name",
                   "appointment_date", "appointment_time", "call_type"):
            _v = outbound_context.get(_k)
            if _v:
                session_data[_k] = _v

    # ── Start session ─────────────────────────────────────────────────────────
    agent = HospitalVoiceAgent(
        system_prompt=system_prompt,
        greeting=greeting,
        tools=tools,
        sensory=sensory,
        hospital_id=hospital_id,
        caller_phone=caller_phone,
        call_id=call_id,
        hospital_name=hospital_name,
        call_started_at=call_started_at,
        agent_language=agent_language,
        premium_llm=_feat_on(tenant_features, "premium_llm"),
        vad=ctx.proc.userdata.get("vad"),
    )

    # Groq free-tier TPM is small (12k). Cap retries so a 429 doesn't hammer the
    # same minute 4x, and limit tool steps so a turn can't chain many large LLM
    # calls.
    session = AgentSession(
        userdata=session_data,
        # Endpointing + preemptive generation moved under turn_handling in
        # livekit-agents 1.5 (the flat kwargs are deprecated, removed in 2.0);
        # this is the exact equivalent the SDK's own compat shim builds.
        #   - preemptive_generation: DISABLED. It starts the LLM the moment the
        #     caller pauses, on the RAW user message — but this agent rewrites
        #     new_message.content in on_user_turn_completed (acoustic metadata,
        #     DTMF remap, repeat-suppression). That mutation invalidates the
        #     preemptive draft, and the turn can end up producing NO reply at all
        #     (observed as Arya going silent right after the caller names a
        #     doctor). Correctness over the small dead-air saving — keep it off.
        #   - endpointing min/max delay: cut the post-speech wait before the LLM
        #     fires (defaults 0.5/3.0s); 0.2/3.0 makes Arya feel near-realtime,
        #     max stays 3.0 so a slow speaker pausing mid-thought isn't cut off.
        turn_handling=TurnHandlingOptions(
            endpointing=EndpointingOptions(
                min_delay=_ENDPOINT_MIN_DELAY, max_delay=_ENDPOINT_MAX_DELAY
            ),
            preemptive_generation=PreemptiveGenerationOptions(enabled=False),
        ),
        max_tool_steps=2,
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=8.0),
        ),
    )

    # Perceived-latency meter: averages EOU + LLM TTFT + TTS TTFB across the call.
    meter = _LatencyMeter()
    session.on("conversation_item_added", meter.on_item)

    # Track whether Arya is currently speaking, and when she last FINISHED speaking
    # (entered the listening state). The inactivity watchdog uses this so the
    # "are you there?" nudge starts counting only once she is actually waiting for
    # the caller — never during or right after her own reply.
    _speech = {"speaking": False, "thinking": False, "since_listening": None,
               "last_user": None, "user_speaking": False}

    def _on_agent_state(ev):
        try:
            st = getattr(ev, "new_state", None) or getattr(ev, "state", None) or ""
            st = str(st).lower()
            if st == "speaking":
                _speech["speaking"] = True
                _speech["thinking"] = False
            elif st == "thinking":
                # LLM is generating and/or a tool (e.g. a multi-doctor availability
                # lookup) is running. Arya — not the caller — is busy here, so the
                # inactivity watchdog must NOT count this window as caller silence.
                # Previously "thinking" fell through as idle, so a slow availability
                # turn tripped the 20s "are you there? I can't hear you." nudge.
                _speech["thinking"] = True
            elif st in ("listening", "idle"):
                # Leaving speaking OR thinking → Arya just finished working; restart
                # the silence window from now so the caller gets the full think-time
                # before any nudge.
                if _speech["speaking"] or _speech["thinking"]:
                    _speech["since_listening"] = datetime.now(timezone.utc)
                _speech["speaking"] = False
                _speech["thinking"] = False
        except Exception:
            pass
    session.on("agent_state_changed", _on_agent_state)

    # Caller-activity tracking for the inactivity watchdog. The previous approach
    # scanned session.history for the last user message's created_at, but that
    # timestamp is stamped when the caller STARTS speaking — which can be EARLIER
    # than `since_listening` (set when Arya finishes her own reply, including the
    # "are you there?" nudge). So a real caller reply looked "older" than Arya's
    # last turn and was ignored, the silence clock never reset, and the nudge
    # fired again every ~20s, looping and poisoning the LLM context with
    # "I can't hear you". Tracking the live user-speech events with a wall-clock
    # `now` is monotonic and always reflects real caller activity.
    def _mark_user_active(_ev=None):
        _speech["last_user"] = datetime.now(timezone.utc)

    def _on_user_state(ev):
        try:
            st = str(getattr(ev, "new_state", None) or getattr(ev, "state", "")).lower()
            _speech["user_speaking"] = (st == "speaking")
            _speech["last_user"] = datetime.now(timezone.utc)
        except Exception:
            pass

    session.on("user_state_changed", _on_user_state)
    session.on("user_input_transcribed", _mark_user_active)

    # Immediate-disconnect diagnostics. A caller who drops before any turn
    # completes (turns=0) is either a benign hangup/misdial or a symptom of
    # pre-greeting dead air. Record HOW LONG they stayed and whether the
    # greeting was delivered so the two cases can be told apart in the logs.
    @ctx.room.on("participant_disconnected")
    def _on_participant_left(participant):
        elapsed = (datetime.now(timezone.utc) - call_started_at).total_seconds()
        _log.info(
            "participant disconnected room=%s call=%s after=%.1fs greeting_delivered=%s",
            room_name, call_id[:8], elapsed,
            session_data.get("greeting_delivered", False),
        )

    # ── Post-call cleanup ─────────────────────────────────────────────────────
    _ambient_task: Optional[asyncio.Task] = None  # assigned after session.start

    async def _on_end_async(_event=None):
        # Only short-circuit if a PRIOR run fully persisted the call. Setting the
        # flag at the TOP let the close-event task set it then get cancelled
        # mid-write, so the reliable awaited shutdown callback skipped the write and
        # the call_log row (+ recording link) was lost. Set it AFTER the write.
        if session_data.get("_ended_done"):
            return
        if _ambient_task:
            _ambient_task.cancel()
        for _t in _drain_tasks:
            _t.cancel()
        try:
            ended_at = datetime.now(timezone.utc)
            total_turns = 0
            transcript: list[dict] = []
            try:
                msgs = session.history.messages()
                non_sys = [m for m in msgs if getattr(m, "role", "") != "system"]
                total_turns = len(non_sys) // 2
                for m in non_sys:
                    content = getattr(m, "content", "")
                    if isinstance(content, (list, tuple)):
                        content = " ".join(str(c) for c in content)
                    transcript.append({"role": getattr(m, "role", ""), "text": str(content)})
            except Exception:
                pass

            ud = session_data
            transfer_dest = ud.get("transfer_destination", "")
            if transfer_dest:
                print(f"[arteq] call ended — transfer to {transfer_dest}")

            # What actually happened on the call (tools mark these via
            # _mark_intent) and how the caller sounded (acoustic tags).
            intents = list(ud.get("intents", []) or [])
            emotional_state = "; ".join(
                dict.fromkeys(ud.get("sensory_events", []) or [])
            )[:500]

            try:
                from src.db.queries import write_call_log
                outcome = transfer_dest if transfer_dest else "completed"
                duration_s = (ended_at - call_started_at).total_seconds()
                # Per-service cost from REAL usage (audio seconds, spoken chars,
                # LLM tokens) × published rate. Telephony here is the duration
                # estimate; the Vobiz CDR job overwrites it with the billed cost.
                _ptok, _ctok = meter.tokens()
                _cost = _cost_breakdown_paise(duration_s, transcript, _ptok, _ctok)
                cost_paise = _cost.cost_paise
                direction = "outbound" if outbound_context else "inbound"
                if total_turns > 0:
                    # Log WHERE the per-turn latency is spent so we can see the
                    # bottleneck in the deploy logs (grep "latency_breakdown"), plus
                    # the cost split (grep "cost_breakdown" — confirms live token capture).
                    _bd = meter.breakdown_ms()
                    print(
                        f"[arteq] latency_breakdown call={call_id} total={meter.avg_ms()}ms "
                        f"eou(vad)={_bd['eou']}ms llm_ttft={_bd['llm']}ms tts_ttfb={_bd['tts']}ms "
                        f"turns={total_turns}",
                        file=sys.stderr, flush=True,
                    )
                    print(
                        f"[arteq] cost_breakdown call={call_id} total={cost_paise}p "
                        f"stt={_cost.stt_paise}p tts={_cost.tts_paise}p llm={_cost.llm_paise}p "
                        f"tel={_cost.telephony_paise}p tokens={_ptok}+{_ctok}",
                        file=sys.stderr, flush=True,
                    )
                else:
                    # No turn ever completed — the caller dropped before the first
                    # exchange. This is NOT an error, so log it at info level (not
                    # stderr) to keep real failures visible. greeting_delivered
                    # distinguishes a benign hangup (True) from pre-greeting dead
                    # air where the agent was too slow to speak (False).
                    _log.info(
                        "call ended with no turns room=%s call=%s greeting_delivered=%s "
                        "duration=%.1fs — caller disconnected before first exchange",
                        room_name, call_id[:8],
                        session_data.get("greeting_delivered", False), duration_s,
                    )
                await write_call_log(
                    hospital_id=hospital_id,
                    call_id=call_id,
                    caller=caller_phone or "unknown",
                    started_at=call_started_at,
                    ended_at=ended_at,
                    total_turns=total_turns,
                    latency_avg_ms=meter.avg_ms(),
                    cost_paise=cost_paise,
                    transcript=transcript,
                    intents=intents,
                    outcome=outcome,
                    emotional_state=emotional_state,
                    stt_paise=_cost.stt_paise,
                    tts_paise=_cost.tts_paise,
                    llm_paise=_cost.llm_paise,
                    telephony_paise=_cost.telephony_paise,
                    llm_prompt_tokens=_ptok,
                    llm_completion_tokens=_ctok,
                    stt_audio_seconds=int(max(duration_s, 0.0)),
                    tts_chars=_spoken_chars(transcript),
                    direction=direction,
                )
            except Exception as log_exc:
                print(f"[arteq] call log write failed: {log_exc}", file=sys.stderr)

            # Persist the recording URL (egress finalises the file as the room closes).
            if session_data.get("recording_url"):
                try:
                    from src.db.queries import get_pool as _gp_rec
                    _rp = await _gp_rec()
                    async with _rp.acquire() as _rc:
                        await _rc.execute(
                            "UPDATE call_logs SET recording_url=$1 WHERE call_id=$2",
                            session_data["recording_url"], call_id,
                        )
                except Exception as _ru_exc:
                    print(f"[arteq] recording_url update failed: {_ru_exc}", file=sys.stderr)

            # Persist patient details captured on the call (even with no booking) so
            # the recording can be named by patient.
            _pm = session_data.get("pmeta") or {}
            if _pm.get("name"):
                try:
                    from src.db.queries import get_pool as _gp_pm
                    _pm_age = _pm.get("age_num")
                    if _pm_age is None:
                        _digits = "".join(ch for ch in str(_pm.get("age", "")) if ch.isdigit())
                        _pm_age = int(_digits) if _digits else None
                    _pp = await _gp_pm()
                    async with _pp.acquire() as _pc:
                        await _pc.execute(
                            "UPDATE call_logs SET patient_name=$1, patient_age=$2, "
                            "patient_gender=$3, patient_age_unit=$4 WHERE call_id=$5",
                            (_pm.get("name") or None), _pm_age,
                            (_pm.get("gender") or None), (_pm.get("age_unit") or "years"), call_id,
                        )
                except Exception as _pm_exc:
                    print(f"[arteq] pmeta update failed: {_pm_exc}", file=sys.stderr)

            # Critical persistence (call_log + recording + patient details) is done;
            # mark complete so a duplicate close/shutdown run no-ops cleanly.
            session_data["_ended_done"] = True

            # Tell live-monitoring subscribers the call is over.
            try:
                from additions.live_events import emit_call_ended
                await emit_call_ended(hospital_id, call_id)
            except Exception:
                pass

            # Auto-redial on early drop: an outbound queue call that was answered
            # but where the patient never engaged (total_turns == 0 — instant drop
            # or dead air) is re-queued once more, capped by the original
            # max_attempts. A call with real turns is left as completed.
            try:
                _rq = (outbound_context or {}).get("_requeue")
                if (_rq and direction == "outbound" and not session_data.get("_requeued")
                        and (total_turns == 0 or session_data.get("voicemail_detected"))):
                    session_data["_requeued"] = True
                    _att = int(_rq.get("attempt", 1))
                    _max = int(_rq.get("max_attempts", 3))
                    if _att < _max:
                        import json as _json2
                        from src.db.queries import get_pool as _get_pool
                        _ctx = {k: v for k, v in (outbound_context or {}).items() if k != "_requeue"}
                        _pool = await _get_pool()
                        async with _pool.acquire() as _conn:
                            await _conn.execute(
                                "INSERT INTO outbound_call_queue "
                                "(hospital_id, call_type, phone, patient_name, context_json, "
                                " scheduled_at, attempt_count, max_attempts, status, tenant_slug) "
                                "VALUES ($1,$2,$3,$4,$5::jsonb, now() + interval '3 minutes', "
                                " $6, $7, 'pending', $8)",
                                _rq.get("hospital_id") or hospital_id,
                                _rq.get("call_type") or "reminder",
                                _rq.get("phone"), _rq.get("patient_name") or "",
                                _json2.dumps(_ctx), _att, _max,
                                _rq.get("tenant_slug") or "default",
                            )
                        _log.info("early_drop_requeued call=%s attempt=%s/%s",
                                  call_id[:8], _att, _max)
            except Exception as _rq_exc:
                print(f"[arteq] early-drop requeue failed: {_rq_exc}", file=sys.stderr)

            # Increment campaign answered counter if this was an outbound campaign call
            campaign_id = (outbound_context or {}).get("campaign_id", "")
            if campaign_id and total_turns > 0:
                try:
                    from src.db.queries import increment_campaign_calls_answered
                    await increment_campaign_calls_answered(campaign_id)
                except Exception as exc:
                    print(f"[arteq] campaign metric update failed: {exc}", file=sys.stderr)

            if getattr(settings, "POST_CALL_SMS_ENABLED", False) and caller_phone:
                from src.services.sms_service import SMSService
                await SMSService().send_call_summary(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    summary=f"Thank you for calling {hospital_name}. We were happy to help.",
                )

            from src.services.staff_alert import StaffAlertService
            outcome_str = transfer_dest or "completed"
            await StaffAlertService().alert_call_summary(
                patient_phone=caller_phone or "unknown",
                turns=total_turns,
                outcome=outcome_str,
                summary="",
                call_id=call_id,
            )
        except Exception as exc:
            print(f"[arteq] post-call cleanup error: {exc}", file=sys.stderr)

    session.on("close", lambda e=None: asyncio.ensure_future(_on_end_async(e)))
    # Also run end-handling as an AWAITED shutdown callback so the call_log write
    # finishes before the job tears down (the close task above gets cancelled
    # mid-write -> CancelledError, which "except Exception" never catches, so the
    # row was silently lost). _ended_once guard + ON CONFLICT make a 2nd run a no-op.
    ctx.add_shutdown_callback(_on_end_async)

    # Start the session immediately — caller is already waiting in silence.
    # The prewarm task runs in the background; if it finishes before on_enter
    # fires the greeting is served from cache (zero TTS latency). If not, on_enter
    # synthesizes the greeting live. Either way the caller hears Arya as soon as
    # the session is ready, without an extra blocking wait here.
    # record=False disables LiveKit Cloud OTLP telemetry export. The exporter
    # blocks on 10s TLS handshakes to the cloud observability endpoint and floods
    # logs with ReadTimeout tracebacks; we don't use cloud recording.
    # Plan gate: inbound AI answering is a FULL-plan feature. On the trial plan
    # only outbound (reminders/confirmations) + dashboard setup are included, so
    # an inbound call is not AI-answered.
    if not outbound_context and getattr(hospital_ctx, "plan", "trial") != "full":
        print(f"[arteq] inbound blocked (trial plan) call={call_id[:8]} hospital={hospital_id}",
              file=sys.stderr)
        try:
            await ctx.room.disconnect()
        except Exception:
            pass
        return

    # DTLN in-process noise suppression — cleans compressed 8kHz phone audio
    # before VAD/STT. Works on self-hosted LiveKit (unlike BVC/Krisp). Stateful
    # LSTM, so one instance per call. DTLN_STRENGTH=0 disables it.
    _dtln_nc = None
    _dtln_strength = float(os.getenv("DTLN_STRENGTH", "0.5"))
    if _dtln_strength > 0:
        try:
            from livekit.plugins.dtln import DTLNNoiseSuppressor
            _dtln_nc = DTLNNoiseSuppressor(strength=_dtln_strength)
            print(f"[arteq] DTLN noise suppression on (strength={_dtln_strength})", file=sys.stderr)
        except Exception as _dtln_exc:
            print(f"[arteq] DTLN unavailable, continuing without it: {_dtln_exc}", file=sys.stderr)

    await session.start(
        agent=agent,
        room=ctx.room,
        record=False,
        # Strip clinic background noise from the inbound audio stream before it
        # reaches VAD or STT. BVCTelephony is the telephony-optimised variant —
        # trained on inbound SIP/PSTN audio so it performs better than the
        # generic BVC on compressed 8kHz phone-call streams.
        # Noise cancellation is configured on the room audio input. RoomInput/
        # OutputOptions were deprecated in livekit-agents 1.5 (removed in 2.0) in
        # favour of RoomOptions; the audio-input defaults are identical, so this
        # is a behaviour-preserving swap.
        room_options=RoomOptions(
            # DTLN in-process noise suppression (self-hosted compatible). Falls back
            # to plain input if DTLN failed to load or DTLN_STRENGTH=0.
            audio_input=(AudioInputOptions(noise_cancellation=_dtln_nc)
                         if _dtln_nc is not None else AudioInputOptions()),
        ),
    )

    # ── Call recording (LiveKit Egress -> /recordings/<call>.ogg) ─────────────
    # Audio-only room composite. The file is served ONLY through the dashboard to
    # authenticated hospital admins (see /hospitals/{id}/calls/{id}/recording).
    if os.getenv("RECORD_CALLS", "true").lower() == "true":
        try:
            from livekit import api as _eg_api
            _egc = _eg_api.LiveKitAPI(
                url=settings.LIVEKIT_URL,
                api_key=settings.LIVEKIT_API_KEY,
                api_secret=settings.LIVEKIT_API_SECRET,
            )
            await _egc.egress.start_room_composite_egress(
                _eg_api.RoomCompositeEgressRequest(
                    room_name=room_name,
                    audio_only=True,
                    file_outputs=[_eg_api.EncodedFileOutput(filepath=f"/recordings/{call_id}.ogg")],
                )
            )
            await _egc.aclose()
            session_data["recording_url"] = (
                f"/admin/api/hospitals/{hospital_id}/calls/{call_id}/recording"
            )
            _log.info("call_recording_started call=%s", call_id[:8])
        except Exception as _eg_exc:
            print(f"[arteq] egress start failed: {_eg_exc}", file=sys.stderr)

    # Background audio: faint office ambience + a keyboard-typing "thinking" sound
    # that LiveKit plays automatically while the agent processes a turn (LLM + tool
    # / DB round-trips) so a lookup never feels like dead silence.
    try:
        from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
        _bg_audio = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.06),
            thinking_sound=[
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.7),
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.6),
            ],
        )
        await _bg_audio.start(room=ctx.room, agent_session=session)
    except Exception as _bg_exc:
        print(f"[arteq] background audio failed, falling back to white noise: {_bg_exc}", file=sys.stderr)
        _ambient_task = asyncio.create_task(_run_ambient_audio(ctx.room))

    # ── Live monitoring: announce the call to dashboard subscribers ───────────
    # (additions/routes/live_ws.py forwards these over the /admin/ws/live
    # socket; with REDIS_URL set the event crosses from this worker process to
    # the web server. Best-effort — monitoring must never break a call.)
    try:
        from additions.live_events import emit_call_started
        await emit_call_started(hospital_id, {
            "call_id": call_id,
            "hospital_id": hospital_id,
            "caller": caller_phone or "unknown",
            "started_at": call_started_at.isoformat(),
            "ended_at": None,
            "outcome": None,
            "intents": [],
        })
    except Exception as exc:
        _log.debug("live event emit failed: %s", exc)

    # ── Cost guardrail: cap call duration ──────────────────────────────────────
    # STT is billed per audio minute, so a phone left off-hook (or a caller who
    # never hangs up) burns money indefinitely. Politely wrap up and drop the
    # room after MAX_CALL_DURATION_S (default 10 min — far above a normal
    # booking call's 2-4 min). Same hangup path as the end_call tool.
    max_call_s = float(os.getenv("MAX_CALL_DURATION_S", "600"))

    async def _duration_watchdog() -> None:
        await asyncio.sleep(max_call_s)
        _log.info("max call duration reached room=%s", room_name)
        try:
            caller_lang = session_data.get("caller_lang", agent_language)
            farewell = _WATCHDOG_FAREWELLS.get(caller_lang, _WATCHDOG_FAREWELLS["en-IN"])
            await session.say(farewell, allow_interruptions=False)
            await asyncio.sleep(8.0)
        except Exception:
            pass
        try:
            from src.services.livekit_sip import delete_room
            await delete_room(room_name)
        except Exception as exc:
            _log.warning("watchdog hangup failed room=%s err=%s", room_name, exc)

    _watchdog = asyncio.create_task(_duration_watchdog())
    session.on("close", lambda e=None: _watchdog.cancel())

    # ── Inactivity watchdog: prompt after 20s silence, hang up after 40s ─────
    _INACTIVITY_PROMPT_S = float(os.getenv("INACTIVITY_PROMPT_S", "20"))
    _INACTIVITY_HANGUP_S = float(os.getenv("INACTIVITY_HANGUP_S", "40"))

    async def _inactivity_watchdog() -> None:
        last_prompt_at = None
        while True:
            await asyncio.sleep(3.0)
            _greet_at = session_data.get("greeting_at")
            if not _greet_at:
                continue
            # Idle = time since the LAST activity by EITHER side. Counting the last
            # ASSISTANT turn too means that right after Arya finishes a (possibly long)
            # reply the caller still gets the full think-time window before any
            # "are you there?" nudge — not counted from their previous message.
            # Never nudge while Arya is speaking or thinking (her processing time is
            # not the caller's silence), nor while the CALLER is mid-utterance.
            if (_speech.get("speaking") or _speech.get("thinking")
                    or _speech.get("user_speaking")):
                continue
            last_turn = datetime.fromtimestamp(_greet_at, tz=timezone.utc)
            # Idle = time since the latest activity by EITHER side:
            #   • since_listening — when Arya last FINISHED speaking (so a long reply's
            #     playout doesn't trip it, and the caller gets full think-time after).
            #   • last_user — wall-clock time of the caller's most recent speech, set
            #     from the live user_state_changed / user_input_transcribed events.
            # last_user is captured with datetime.now() at the moment the caller
            # speaks, so it is always current and never "older" than since_listening
            # the way the old history-scan (which used the message's start-of-speech
            # created_at) could be — that ordering bug let the nudge loop forever.
            for _cand in (_speech.get("since_listening"), _speech.get("last_user")):
                if _cand and _cand > last_turn:
                    last_turn = _cand

            silence_s = (datetime.now(timezone.utc) - last_turn).total_seconds()

            # Prompt after PROMPT_S of silence and RE-PROMPT every PROMPT_S while the
            # caller stays silent (was: prompted only once).
            if silence_s >= _INACTIVITY_PROMPT_S and (
                last_prompt_at is None
                or (datetime.now(timezone.utc) - last_prompt_at).total_seconds() >= _INACTIVITY_PROMPT_S
            ):
                last_prompt_at = datetime.now(timezone.utc)
                try:
                    lang = session_data.get("caller_lang", agent_language)
                    _SILENCE_PROMPTS = {
                        "ml-IN": "നിങ്ങൾ അവിടെ ഉണ്ടോ? എനിക്ക് കേൾക്കാൻ കഴിയുന്നില്ല.",
                        "en-IN": "Are you there? I can't hear you.",
                        "hi-IN": "क्या आप वहाँ हैं? मुझे सुनाई नहीं दे रहा।",
                        "ta-IN": "நீங்கள் அங்கே இருக்கிறீர்களா? கேட்கவில்லை.",
                    }
                    prompt_text = _SILENCE_PROMPTS.get(lang, _SILENCE_PROMPTS["en-IN"])
                    await session.say(prompt_text, allow_interruptions=True)
                except Exception:
                    pass

            if silence_s >= _INACTIVITY_HANGUP_S:
                try:
                    lang = session_data.get("caller_lang", agent_language)
                    _SILENCE_FAREWELLS = {
                        "ml-IN": "ശ്രദ്ധിക്കുക, ഞാൻ കോൾ അവസാനിപ്പിക്കുന്നു. നന്ദി!",
                        "en-IN": "I'll end the call now. Thank you for calling, goodbye!",
                        "hi-IN": "मैं कॉल समाप्त कर रहा हूँ। धन्यवाद, अलविदा!",
                        "ta-IN": "அழைப்பை முடிக்கிறேன். அழைத்தமைக்கு நன்றி, வணக்கம்!",
                    }
                    farewell = _SILENCE_FAREWELLS.get(lang, _SILENCE_FAREWELLS["en-IN"])
                    await session.say(farewell, allow_interruptions=False)
                    await asyncio.sleep(5.0)
                    from src.services.livekit_sip import delete_room
                    await delete_room(room_name)
                except Exception as exc:
                    _log.warning("inactivity_hangup_failed room=%s err=%s", room_name, exc)
                break

    _inactivity_task = asyncio.create_task(_inactivity_watchdog())
    session.on("close", lambda e=None: _inactivity_task.cancel())


def prewarm(proc) -> None:
    """Load the Silero VAD model once per worker process, before any call.

    Silero load is the heaviest per-call setup step; doing it here keeps it off
    the critical path so the first turn responds sooner.
    """
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=_VAD_MIN_SILENCE,
        activation_threshold=_VAD_ACTIVATION,
        min_speech_duration=_VAD_MIN_SPEECH,
    )


if __name__ == "__main__":
    # LiveKit Cloud uses explicit dispatch. The token endpoint (src/main.py)
    # attaches RoomAgentDispatch(agent_name=LIVEKIT_DISPATCH_NAME) so this worker
    # joins the room on creation. Name MUST match the token side. Override
    # LIVEKIT_DISPATCH_NAME locally to isolate a dev worker from prod.
    from src.config.settings import settings as _settings
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        agent_name=_settings.LIVEKIT_DISPATCH_NAME,
    ))
