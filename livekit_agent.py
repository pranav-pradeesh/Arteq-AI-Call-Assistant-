"""
Arteq Hospital Voice Agent — LiveKit 1.5.x edition.

Full-featured AI receptionist for Kerala hospitals:
  • Silero VAD → Sarvam STT (Saaras v3, 23 languages, codemix)
  • Groq LLaMA 70B (via OpenAI-compatible base_url)
  • Sarvam TTS (Bulbul v3, Malayalam, "shubh" voice)
  • Acoustic Sensory Layer — detects patient distress from PCM stats
  • Function tools — book/cancel appointments, callbacks, SMS, emergency
  • Multi-tenant — room name = "{slug}-call-{uuid}", context from DB

Run:
  python livekit_agent.py dev      # development (auto-join)
  python livekit_agent.py start    # production worker pool

Required env vars:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
  SARVAM_API_KEY, GROQ_API_KEY
  DATABASE_URL
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("livekit.agents")

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, APIConnectOptions, RoomInputOptions
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
        from src.cache.store import tts_cache, TTS_CACHE_TTL

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
            await ws.configure(target_language_code=lang, speaker=speaker)
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

    async def _run_rest(
        self, output_emitter, text: str, lang: str, speaker: str, cache_key: str
    ) -> None:
        """REST TTS fallback — waits for full synthesis before any audio plays."""
        from src.cache.store import tts_cache, TTS_CACHE_TTL

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
                request_id = response_json.get("request_id", "")
                audios = response_json.get("audios", [])
                if not audios or not isinstance(audios, list):
                    raise _APIConnErr("Sarvam TTS API response invalid: no audio data")
                output_emitter.initialize(
                    request_id=request_id or "unknown",
                    sample_rate=self._tts.sample_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/wav",
                )
                decoded = [_b64.b64decode(b64) for b64 in audios]
                for chunk in decoded:
                    output_emitter.push(chunk)
                tts_cache.set(cache_key, decoded, ttl=TTS_CACHE_TTL)
        except asyncio.TimeoutError as e:
            raise _APITimeoutErr("Sarvam TTS API request timed out") from e
        except _aiohttp.ClientError as e:
            raise _APIConnErr(f"Sarvam TTS API connection error: {e}") from e


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

# Keep system prompt + last 12 messages (6 turns). A booking spans several
# turns (name → doctor → date → time → confirm); at 2 turns the agent forgot
# what the caller already said. The per-turn cost is dominated by the large
# system prompt re-sent every turn, so a few short history messages are cheap.
_MAX_CTX = 12

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
# Amplitude 0.06 (6 % of int16 max) keeps it well below conversational speech.
_rng = np.random.default_rng(seed=0)
_raw = _rng.standard_normal(5 * _AMBIENT_SAMPLE_RATE + 7).astype(np.float64)
_raw = np.convolve(_raw, np.ones(7) / 7, mode="valid")
_raw /= np.abs(_raw).max()
_AMBIENT_BUF = (_raw * 0.06 * 32767).astype(np.int16)
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


class _GroqLLM(openai.LLM):
    """Groq LLM that injects anti-repetition penalties on every turn.

    The voice pipeline calls ``chat()`` internally, so there's no per-call hook to
    pass penalties through — we set them here. frequency_penalty curbs the same
    word recurring within a reply; presence_penalty pushes the model to introduce
    new wording instead of echoing the caller's phrasing back. Both are Groq/OpenAI
    chat params; Sarvam's leg stays plain (it may reject them).
    """

    def chat(self, **kwargs):
        ek = kwargs.get("extra_kwargs")
        extra = dict(ek) if isinstance(ek, dict) else {}
        extra.setdefault("frequency_penalty", 0.4)
        extra.setdefault("presence_penalty", 0.3)
        kwargs["extra_kwargs"] = extra
        return super().chat(**kwargs)


def _build_llm(premium: bool = True):
    """Resilient LLM with a 3-leg fallback chain: 70b → 8b → Sarvam.

    70b is the primary for EVERY tenant: the 8b model is too weak at Malayalam +
    multi-rule prompt adherence — on live calls it parroted the caller's question
    back instead of acting on it and transliterated English words ("ഇയേസ്" for
    "Yes") despite the SCRIPT rule. 70b follows both reliably and is free on Groq.

    The chain matters because Groq's free tier enforces a *per-model* daily token
    cap (TPD, 100k): when 70b's cap is exhausted it 429s every turn. Each model
    has its OWN bucket, so llama-3.1-8b-instant keeps serving — and it's
    sub-second, vs Sarvam's ~12s. So 8b is the fast middle leg; Sarvam (Indian-
    language, fully separate provider/quota) is the last resort that guarantees
    Arya never goes silent even if all of Groq is down.

    Sarvam's OpenAI-compatible endpoint authenticates with an
    `api-subscription-key` header, not Bearer, so it needs a custom client.
    """
    def _groq(model: str) -> openai.LLM:
        return _GroqLLM(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY", ""),
            # Malayalam script is token-dense, so 200 truncates a 2-sentence
            # reply mid-word; 512 fits a full reply. 0.5 adds enough variation to
            # sound human without letting llama-3.3 drift into emitting its
            # <function=...> tool syntax as spoken text.
            model=model,
            max_completion_tokens=200,
            temperature=0.5,
        )

    # premium retained for signature compat; both tiers now lead with 70b for
    # quality, with 8b as the fast middle leg when 70b's daily cap is hit.
    chain = [_groq("llama-3.3-70b-versatile"), _groq("llama-3.1-8b-instant")]

    sarvam_key = os.getenv("SARVAM_API_KEY", "")
    if sarvam_key:
        chain.append(openai.LLM(
            # sarvam-m was deprecated by Sarvam (returns 400). sarvam-30b is the
            # current Indian-language chat model — separate provider, so it keeps
            # answering when all Groq legs are capped. ~12s latency, hence last.
            model="sarvam-30b",
            temperature=0.4,
            client=_AsyncOpenAI(
                api_key=sarvam_key,
                base_url="https://api.sarvam.ai/v1",
                default_headers={"api-subscription-key": sarvam_key},
            ),
        ))

    if len(chain) == 1:
        return chain[0]
    return agents_llm.FallbackAdapter(chain)


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
        hospital_id = str(row["id"]) if row else settings.HOSPITAL_ID
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

def _build_prompt(hospital_ctx, outbound_context: Optional[dict]) -> str:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(_IST)
    _DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    day_name = _DAYS[(now.weekday() + 1) % 7]
    time_str = now.strftime("%H:%M")

    if hospital_ctx:
        try:
            from src.ai.groq_brain import _build_hospital_summary
            hosp_block = _build_hospital_summary(hospital_ctx)
        except Exception:
            hosp_block = f"Hospital: {hospital_ctx.name}"
        from src.tts_normalize import name_for_lang
        _lang = getattr(hospital_ctx, "agent_language", "ml-IN") or "ml-IN"
        hosp_name = name_for_lang(hospital_ctx.name, hospital_ctx.name_ml or "", _lang)
        dow = (now.weekday() + 1) % 7
        hours = hospital_ctx.hours_for_day(dow)
        if hours:
            open_t, close_t = hours
            open_status = (
                f"OPEN {open_t}–{close_t}"
                if open_t <= time_str <= close_t
                else f"CLOSED (opens {open_t})"
            )
        else:
            open_status = "Hours not listed"
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
                f"You are calling to confirm {pname}'s appointment with Dr. {dname} "
                f"on {date} at {ttime}.\n"
                "First sentence: state the appointment and ask if they can attend.\n"
                "If YES → use book_appointment to confirm. If NO → offer to reschedule.\n"
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

LANGUAGE: Default to the hospital's configured language. Reply in the same language and script as the caller's most recent message — Malayalam, English, Hindi, Tamil, Kannada, Telugu, Bengali, Gujarati, Punjabi, Odia, Marathi, or Manglish (Malayalam in Latin script). Never switch to English unless the caller spoke English first. Match script exactly: Malayalam → Malayalam script, Hindi/Marathi → Devanagari, Tamil → Tamil script, Telugu → Telugu script, Kannada → Kannada script, Bengali → Bengali script, Gujarati → Gujarati script, Punjabi → Gurmukhi, Odia → Odia script. Keep replies to at most 2 short sentences and end with ONE question when you need something. Speak plainly and naturally.

SCRIPT: Keep these terms in English (Latin script) exactly as Keralites say them — do NOT transliterate: doctor, appointment, OPD, token, lab, scan, report, casualty, emergency, timing, consultation, booking. Bulbul TTS pronounces Latin letters in English automatically; transliterating them breaks pronunciation.

MALAYALAM STYLE: Use everyday spoken Malayalam (സംസാരഭാഷ) — warm and simple, never literary or Sanskritic. Say "എന്താണ് വേണ്ടത്?" not "എന്ത് ആവശ്യമാണ്?". Verbs take no gender suffix: "വന്നു" not "വന്നാൾ". Speak times as: "രാവിലെ 10 മണി", "ഉച്ചയ്ക്ക് 2 മണി" — never "10 AM" or "10:00 AM". For Manglish callers (Malayalam in Latin script), reply in Manglish matching their mix.

GRAMMAR (apply per reply language — speak like a real native, not a translation):
Hindi/Marathi: "आप"/"तुम्ही" (formal). Verb agrees with subject gender; use masculine default if gender unknown. Times: "सुबह दस बजे"/"सकाळी दहा वाजता". Spoken questions tag "ना?" or end with "क्या?".
Tamil: "நீங்கள்" (formal). Questions end "-ஆ?". Time: "காலை 10 மணி". Spoken contraction: "வாங்க" not "வாரும்". Never "நீர்" (archaic).
Telugu: "మీరు" (formal). Questions end "-ఆ?". Time: "ఉదయం 10 గంటలు". Use everyday speech forms, not literary Telugu.
Kannada: "ನೀವು" (formal). Questions end "-ಆ?". Time: "ಬೆಳಿಗ್ಗೆ 10 ಗಂಟೆ". Use "-ರಿ" honorific suffix on verbs.
Bengali: "আপনি" (formal). Questions add "কি?" at end. Time: "সকাল দশটা". Verb endings: "-চ্ছেন" (progressive), "-লেন" (past).
Gujarati: "આપ" (formal). Time: "સવારે દસ". Questions: "-ને?" suffix.
Punjabi: "ਆਪ" (formal). Time: "ਸਵੇਰੇ ਦਸ ਵਜੇ". Respectful verb suffix "-ਜੀ".
Odia: "ଆପଣ" (formal). Time: "ସକାଳ ୧୦ ଟା". Questions end "-କି?".

ONE QUESTION AT A TIME: Ask for only ONE missing piece per turn — never bundle questions (do NOT say "what is your name, doctor and date?"). For booking, collect in this order, one per turn: name → date → time. Wait for the answer before asking the next.

NAME COLLECTION: When asking for the caller's name for the first time, use these exact phrasings — natural, warm, not robotic:
Malayalam/Manglish → "ഒന്ന് പേര് പറഞ്ഞോ?" | Hindi → "आपका नाम बताइए?" | Tamil → "உங்கள் பெயர் சொல்லுங்கள்?" | Telugu → "మీ పేరు చెప్పండి?" | Kannada → "ನಿಮ್ಮ ಹೆಸರು ಹೇಳಿ?" | Bengali → "আপনার নাম বলুন?" | English → "Could I get your name?"

CONTEXT MEMORY: Your full conversation history is visible — use it actively. Never re-ask for something the caller already said this call: their name, doctor preference, date, symptoms, reason. Reference what they told you: "ഡോ. രാജൻ — Monday 10 AM ആണോ?" or "You mentioned Dr. Rajan — confirming Monday at 10?" If a caller corrects you, update and confirm the new value. Never say "I don't have that information" about something they said earlier in this same call.

PUNCTUATION: Use full, natural punctuation — commas for pauses, a full stop to end, a question mark on questions — the voice uses it for intonation. Do NOT repeat the same word back-to-back, and do NOT echo the caller's exact words; rephrase.

ANSWER INSTANTLY from the HOSPITAL section below — NO tool, NO "let me check" — for: whether a department exists, its floor/location, operating hours, open/closed, doctor names and their department, emergency numbers, address, phone, and anything in the HANDBOOK. You already know these; just say the answer.

USE A TOOL ONLY for live data or write actions, and call it SILENTLY: check_availability (is a doctor free), book_appointment (collect name+doctor+date+time), reschedule_appointment, cancel_appointment, get_doctor_schedule (exact timings), request_callback, send_location_sms, transfer_to_department, alert_emergency, end_call (hang up when the caller is done). Before booking, repeat name, doctor, date and time back to confirm.

DATE & TIME: Silently convert the caller's words to an absolute date and 24-hour time before calling a tool. Use TODAY (below) as the reference: "tomorrow" = today + 1, "day after" = today + 2, a weekday name = its next occurrence. Pass date as YYYY-MM-DD and time as HH:MM (e.g. "10 in the morning" → 10:00, "3 pm" → 15:00). NEVER speak the conversion or the current clock time back to the caller — just use it. If you can't tell the day or time, ask for it in one short question — never guess.

ENDING THE CALL: When the caller signals they are finished — "ok thanks", "that's all", "no, nothing else", "goodbye" — do NOT ask another question and do NOT re-offer help. Say ONE short farewell and call end_call. Only keep the conversation going if they actually raise a new request.

NEXT-AVAILABLE DOCTOR: When the caller asks for any available doctor / a department/specialty (e.g. "a cardiologist", "whichever doctor is free soonest") rather than a named doctor, NEVER repeat their request back as a question and NEVER make them choose. Pick ONE doctor in that department, call check_availability, and STATE the soonest open slot directly ("Dr. X is free tomorrow at 10:00 — shall I book that?"). Only offer another doctor if that one has no slots or the caller declines.

NEVER say a department or doctor listed in the HOSPITAL section is unavailable or does not exist. If the caller names a specialty (e.g. "a cardiology doctor"), pick a doctor from that department and proceed — do NOT reply "no doctor available". If they don't know any name, briefly list that department's doctors and ask which one. Only after check_availability returns zero slots may you say that specific doctor has no slots that day.

NEVER invent doctor names, timings, fees, or availability — if it is neither in the HOSPITAL section nor a tool result, transfer.

CRITICAL: Your spoken reply is plain natural language ONLY. NEVER write code, JSON, or function/tool syntax (no "<function=...>", no "{...}"). NEVER announce or narrate tool use — do NOT say "I am calling a function", "let me check", "fetching details", "one moment" or anything similar. Speak ONLY the final answer.

If a [SENSORY:...] tag shows TENSION=TREMBLING or VOL/PITCH=LOW → the caller may be in pain or frightened: speak gently, reassure first.

EMERGENCY (chest pain, severe bleeding, unconscious, can't breathe, stroke, poisoning): call alert_emergency FIRST, say "Connecting you to emergency — please stay on the line."

DIGITS: 1=OPD/doctor 2=emergency 3=lab 4=pharmacy 5=billing 0=reception *=repeat

AFTER HOURS: if CLOSED, give next opening and offer (a) book for then, (b) callback, or (c) emergency. Never say "closed, goodbye".
{outbound_block}
HOSPITAL:
{hosp_block}

TODAY: {day_name}, {time_str} IST | STATUS: {open_status}"""


def _build_greeting(hospital_ctx, outbound_context: Optional[dict],
                    returning_name: str = "", agent_language: str = "ml-IN") -> str:
    # Fixed text per call type. Inbound uses a time-of-day Malayalam greeting so the
    # audio is identical per hour-bucket → first call of each bucket warms the TTS
    # cache, every subsequent call is an instant cache hit.
    from src.tts_normalize import name_for_lang
    # Outbound calls are always English-language, so always use the Latin name.
    # Inbound uses the native-script name for Indic langs so TTS phonetics are correct.
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
        date  = outbound_context.get("appointment_date", "")
        ttime = outbound_context.get("appointment_time", "")
        if call_type == "confirmation":
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"I'm calling to confirm your appointment with Dr. {dname} on {date} at {ttime}. "
                "Can you attend?"
            )
        elif call_type == "reminder":
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"This is a reminder of your appointment with Dr. {dname} on {date}. "
                "Do you have any questions?"
            )
        elif call_type == "callback":
            return f"Hello {pname}, this is {hosp_name} calling. How can I help you today?"
        elif call_type == "followup":
            return (
                f"Hello {pname}, this is {hosp_name} calling. "
                f"How are you feeling after your visit with Dr. {dname}?"
            )

    # Inbound: language-appropriate greeting, time-of-day for Malayalam.
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
        super().__init__(
            instructions=system_prompt,
            tools=tools,
            stt=sarvam.STT(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                model="saaras:v3",
                language="unknown",
            ),
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
                min_silence_duration=0.2,
                activation_threshold=0.5,
                min_speech_duration=0.3,
            ),
            llm=_build_llm(premium=premium_llm),
            tts=BulbulV3TTS(
                api_key=os.getenv("SARVAM_API_KEY", ""),
                # Bulbul requires the target language code; without it the
                # request 400s and no audio is produced. Model (bulbul:v3),
                # speaker and 24000 Hz sample rate are enforced by BulbulV3TTS.
                target_language_code=agent_language,
                speaker="priya",
            ),
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

        # Drop sub-threshold transcripts. VAD's min_speech_duration=0.3s already
        # filters most noise bursts, but occasional artefacts still produce a
        # 1-character STT output (a stray vowel, a click). A real utterance is
        # always ≥2 chars. Skip silently — no LLM call, no reply.
        if stripped and len(stripped) < 2 and stripped not in _DTMF:
            return

        # Remember the caller's language (script-detected) so the live backchannel
        # murmurs in their language, not always Malayalam. Callers rarely switch
        # language mid-call, so the previous turn's detection is a safe predictor.
        if stripped:
            try:
                self.session.userdata["caller_lang"] = _detect_tts_lang(stripped, self._agent_language)
            except Exception:
                pass

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
        """
        async def _clean():
            buf = ""
            async for chunk in text:
                buf += chunk
                buf = _TOOL_SYNTAX_COMPLETE_RE.sub("", buf)   # drop closed blocks
                emit, buf = _split_safe(buf)                   # hold only tag-prefix tail
                if emit:
                    yield emit
            tail = _strip_tool_syntax(buf)                     # flush any unterminated tail
            if tail:
                yield tail

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


# Provider rates (paise). Tune via env if pricing changes; defaults reflect
# Sarvam's published list as of 2026-06: Saaras STT ₹30/audio-hr, Bulbul TTS
# ₹0.30 per 1000 chars. Groq LLaMA on the free tier costs us ~0 per call.
_STT_PAISE_PER_MIN = float(os.getenv("STT_PAISE_PER_MIN", "50"))    # ₹30/hr = 50 paise/min
_TTS_PAISE_PER_KCHAR = float(os.getenv("TTS_PAISE_PER_KCHAR", "30"))  # ₹0.30/1000 chars
_LLM_PAISE_PER_TURN = float(os.getenv("LLM_PAISE_PER_TURN", "0"))   # Groq free tier


def _estimate_cost_paise(duration_s: float, transcript: list[dict]) -> int:
    """Rough per-call cost in paise. STT bills on call duration (it transcribes
    the whole audio stream), TTS bills on the characters Arya actually spoke, and
    the LLM is ~free on Groq. Good enough to surface a per-call rupee figure on
    the dashboard and catch a runaway-cost regression — not an invoice."""
    minutes = max(duration_s, 0.0) / 60.0
    stt = minutes * _STT_PAISE_PER_MIN
    spoken_chars = sum(
        len(m.get("text", "")) for m in transcript if m.get("role") == "assistant"
    )
    tts = (spoken_chars / 1000.0) * _TTS_PAISE_PER_KCHAR
    llm = (len(transcript) // 2) * _LLM_PAISE_PER_TURN
    return max(0, round(stt + tts + llm))


class _LatencyMeter:
    """Running average of perceived response latency — the time from the caller
    finishing their turn to Arya's first audio. Built from the per-stage metrics
    LiveKit emits on `metrics_collected`: end-of-utterance delay (VAD settle) +
    LLM time-to-first-token + TTS time-to-first-byte. Each stage is averaged
    independently (they don't all fire on every turn), then summed, so a missing
    metric on one turn doesn't skew the total. Surfaced as latency_avg_ms on the
    call log to catch a prod latency regression we can't otherwise see."""

    def __init__(self) -> None:
        self._sum = {"eou": 0.0, "llm": 0.0, "tts": 0.0}
        self._cnt = {"eou": 0, "llm": 0, "tts": 0}

    def on_metrics(self, ev) -> None:
        m = getattr(ev, "metrics", ev)
        # Duck-type by attribute: metric classes vary across plugin versions.
        for attr, key in (("end_of_utterance_delay", "eou"), ("ttft", "llm"), ("ttfb", "tts")):
            val = getattr(m, attr, None)
            if isinstance(val, (int, float)) and val > 0:
                self._sum[key] += float(val)
                self._cnt[key] += 1

    def avg_ms(self) -> int:
        total_s = sum(
            (self._sum[k] / self._cnt[k]) for k in self._sum if self._cnt[k]
        )
        return round(total_s * 1000)



# ==============================================================================
# Agent entrypoint
# ==============================================================================

async def _run_ambient_audio(room: rtc.Room) -> None:
    """Publish low-level background noise as a second audio track for the call.

    The LiveKit SIP gateway mixes all tracks from a participant before sending
    to PSTN, so this ambient sound is heard by the caller throughout. Task
    cancellation is the shutdown signal — finally block unpublishes cleanly.
    """
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
        for p in ctx.room.remote_participants.values():
            ident = p.identity or p.name or ""
            if ident.startswith("+") or (ident.startswith("91") and len(ident) >= 12):
                caller_phone = ident if ident.startswith("+") else f"+{ident}"
                break
        from src.tenancy.features import enabled as _feat_on
        if caller_phone and _feat_on(tenant_features, "patient_recognition"):
            patient_profile = await _load_patient_profile(caller_phone, hospital_id)
    except Exception:
        pass

    # ── Build system prompt ───────────────────────────────────────────────────
    from src.config.settings import settings
    # Per-hospital language overrides the global env var default. The agent has no
    # spoken name — it identifies only by the hospital — so no agent_name is used.
    agent_language = (getattr(hospital_ctx, "agent_language", None) or settings.AGENT_LANGUAGE)
    system_prompt = _build_prompt(hospital_ctx, outbound_context)
    if patient_profile:
        last = patient_profile["history"][0] if patient_profile["history"] else {}
        system_prompt += (
            f"\n\nRETURNING PATIENT: {patient_profile['name']} — "
            f"last seen {last.get('slot', 'recently')} with Dr. {last.get('doctor', '?')}. "
            "Greet them by name."
        )

    returning_name = patient_profile["name"] if (patient_profile and not outbound_context) else ""
    greeting = _build_greeting(hospital_ctx, outbound_context, returning_name, agent_language)
    # Start synthesizing the greeting immediately so it is in the TTS cache
    # before on_enter fires. Runs in parallel with all remaining setup work.
    _prewarm_task = asyncio.create_task(_prewarm_greeting_audio(greeting, agent_language))
    asyncio.create_task(_prewarm_static_phrases(agent_language))

    # ── Tool set (tier baseline, then per-tenant feature gating) ───────────────
    from src.telephony.livekit_tools import ALL_TOOLS, CLINIC_TOOLS
    from src.tenancy.features import enabled as _feat_on
    tools = list(CLINIC_TOOLS if hospital_tier == "clinic" else ALL_TOOLS)
    def _tool_name(t) -> str:
        return getattr(t, "name", None) or getattr(t, "__name__", "")
    if not _feat_on(tenant_features, "multi_department_routing"):
        tools = [t for t in tools if _tool_name(t) != "transfer_to_department"]

    # ── Acoustic sensory layer ────────────────────────────────────────────────
    sensory = AcousticSensoryLayer()

    @ctx.room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        stream = rtc.AudioStream(track)

        async def _drain():
            async for frame in stream:
                sensory.feed(frame)

        asyncio.create_task(_drain())

    # ── Session userdata (accessible inside tools via context.userdata) ───────
    session_data = {
        "hospital_id":         hospital_id,
        "hospital_ctx":        hospital_ctx,
        "hospital_name":       hospital_name,
        "caller_phone":        caller_phone,
        "call_id":             call_id,
        "room_name":           room_name,
        "transfer_requested":  False,
        "transfer_destination": "",
        "caller_lang":         "ml-IN",
        "intents":             [],   # appended by tools via _mark_intent
        "sensory_events":      [],   # acoustic [SENSORY:...] tags per turn
    }

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

    # Groq free-tier TPM is small (12k). Disable preemptive generation (it fires
    # a second LLM call that our on_user_turn_completed mutation invalidates),
    # cap retries so a 429 doesn't hammer the same minute 4x, and limit tool
    # steps so a turn can't chain many large LLM calls.
    session = AgentSession(
        userdata=session_data,
        # Start the LLM the moment the caller pauses, before end-of-turn is fully
        # confirmed, then keep or discard the draft once VAD settles. Removes most
        # of the post-speech dead air, so replies feel near-instant.
        preemptive_generation=True,
        # Cut the post-speech wait before the LLM fires. Defaults are 0.5/6.0s;
        # 0.2/3.0 makes Arya feel near-realtime. max stays 3.0 so a slow speaker
        # who keeps talking past a pause still isn't cut off.
        min_endpointing_delay=0.2,
        max_endpointing_delay=3.0,
        max_tool_steps=2,
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=8.0),
        ),
    )

    # Perceived-latency meter: averages EOU + LLM TTFT + TTS TTFB across the call.
    meter = _LatencyMeter()
    session.on("metrics_collected", meter.on_metrics)

    # ── Post-call cleanup ─────────────────────────────────────────────────────
    _ambient_task: Optional[asyncio.Task] = None  # assigned after session.start

    async def _on_end_async(_event=None):
        if _ambient_task:
            _ambient_task.cancel()
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
                cost_paise = _estimate_cost_paise(duration_s, transcript)
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
                )
            except Exception as log_exc:
                print(f"[arteq] call log write failed: {log_exc}", file=sys.stderr)

            # Tell live-monitoring subscribers the call is over.
            try:
                from additions.live_events import emit_call_ended
                await emit_call_ended(hospital_id, call_id)
            except Exception:
                pass

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

    # Start the session immediately — caller is already waiting in silence.
    # The prewarm task runs in the background; if it finishes before on_enter
    # fires the greeting is served from cache (zero TTS latency). If not, on_enter
    # synthesizes the greeting live. Either way the caller hears Arya as soon as
    # the session is ready, without an extra blocking wait here.
    # record=False disables LiveKit Cloud OTLP telemetry export. The exporter
    # blocks on 10s TLS handshakes to the cloud observability endpoint and floods
    # logs with ReadTimeout tracebacks; we don't use cloud recording.
    await session.start(
        agent=agent,
        room=ctx.room,
        record=False,
        # Strip clinic background noise from the inbound audio stream before it
        # reaches VAD or STT. BVCTelephony is the telephony-optimised variant —
        # trained on inbound SIP/PSTN audio so it performs better than the
        # generic BVC on compressed 8kHz phone-call streams.
        # NOTE: livekit-agents 1.x takes this here via RoomInputOptions, NOT as
        # an AgentSession kwarg — passing it to AgentSession() raises TypeError.
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    # Start ambient background noise loop — must be after session.start so the
    # local_participant is fully connected. _on_end_async reads this variable
    # by name from the enclosing scope (Python late-binding) and cancels it.
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


def prewarm(proc) -> None:
    """Load the Silero VAD model once per worker process, before any call.

    Silero load is the heaviest per-call setup step; doing it here keeps it off
    the critical path so the first turn responds sooner.
    """
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=0.2,
        activation_threshold=0.5,
        min_speech_duration=0.3,
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
