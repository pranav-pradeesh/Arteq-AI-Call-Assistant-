"""
Exotel Voicebot / AgentStream WebSocket protocol — pure (de)serialization.

This module is the audio-format heart of the Exotel WebSocket integration. It
has NO LiveKit or network dependency so it can be unit-tested in isolation; the
bridge in ``exotel_bridge.py`` wires it to a LiveKit room.

Audio format (per Exotel AgentStream spec)
-------------------------------------------
Media payloads are ``raw/slin``: **16-bit signed linear PCM, 8 kHz, mono,
little-endian**, base64-encoded. The same format is expected back from us for
bidirectional (Voicebot) playback to the caller.

Outgoing chunk rules (bidirectional only)
-----------------------------------------
Each ``media`` frame we send back MUST be a multiple of 320 bytes, at least
3200 bytes (≈100 ms) and at most 100000 bytes. Non-multiple sizes introduce
~20 ms gaps; oversized frames may time out.

Messages FROM Exotel:  connected → start → media* → (dtmf) → stop
Messages TO Exotel  :  media (audio back), mark (playback checkpoint),
                       clear (flush buffered playback — used for barge-in)

Note on casing: Exotel sends ``stream_sid`` (snake) or ``streamSid`` (camel)
depending on the flow, so we accept both on the way in. On the way out we emit
camelCase ``streamSid`` to match working bidirectional clients (e.g. pipecat) —
Exotel keys playback on ``streamSid`` and drops the connection if the outbound
``media`` frame doesn't match, so the casing matters here.

Reference: https://developer.exotel.com/docs/agentstream/developer-guide
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Iterator

# ── Audio format constants ─────────────────────────────────────────────────────

EXOTEL_SAMPLE_RATE = 8000        # Hz
EXOTEL_NUM_CHANNELS = 1          # mono
EXOTEL_BYTES_PER_SAMPLE = 2      # 16-bit signed PCM
EXOTEL_BYTES_PER_SEC = EXOTEL_SAMPLE_RATE * EXOTEL_NUM_CHANNELS * EXOTEL_BYTES_PER_SAMPLE  # 16000

# Sample rates Exotel can negotiate (via the applet URL's ?sample-rate param).
# Exotel streams 16 kHz by default and only downsamples to 8 kHz when the URL
# carries ?sample-rate=8000, so the bridge must honour whatever the start event
# declares rather than assuming 8 kHz.
SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000)

# Outgoing chunk constraints (bytes).
CHUNK_MULTIPLE = 320
MIN_CHUNK_BYTES = 3200           # ≈100 ms
MAX_CHUNK_BYTES = 100000


# ── Inbound event parsing ──────────────────────────────────────────────────────

@dataclass
class StreamStart:
    """Parsed ``start`` event — the call's identity and stream metadata."""
    stream_sid: str
    call_sid: str = ""
    account_sid: str = ""
    from_number: str = ""
    to_number: str = ""
    custom_parameters: dict[str, str] = field(default_factory=dict)
    media_format: dict[str, Any] = field(default_factory=dict)


def parse_message(raw: str | bytes) -> dict[str, Any]:
    """Decode a WebSocket text frame into a dict. Returns {} on malformed JSON."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        msg = json.loads(raw)
        return msg if isinstance(msg, dict) else {}
    except (ValueError, TypeError):
        return {}


def event_type(msg: dict[str, Any]) -> str:
    """Return the lowercased ``event`` field ("" if absent)."""
    return str(msg.get("event", "")).lower()


def extract_stream_sid(msg: dict[str, Any]) -> str:
    """Pull the stream sid from any event, tolerating snake/camel and nesting."""
    for key in ("stream_sid", "streamSid"):
        val = msg.get(key)
        if val:
            return str(val)
    start = msg.get("start") or {}
    if isinstance(start, dict):
        for key in ("stream_sid", "streamSid"):
            val = start.get(key)
            if val:
                return str(val)
    return ""


def parse_start(msg: dict[str, Any]) -> StreamStart:
    """Parse a ``start`` event payload into a StreamStart."""
    start = msg.get("start") or {}
    if not isinstance(start, dict):
        start = {}
    params = start.get("custom_parameters") or start.get("customParameters") or {}
    if not isinstance(params, dict):
        params = {}
    media_format = start.get("media_format") or start.get("mediaFormat") or {}
    if not isinstance(media_format, dict):
        media_format = {}
    return StreamStart(
        stream_sid=extract_stream_sid(msg),
        call_sid=str(start.get("call_sid") or start.get("callSid") or ""),
        account_sid=str(start.get("account_sid") or start.get("accountSid") or ""),
        from_number=str(start.get("from") or ""),
        to_number=str(start.get("to") or ""),
        custom_parameters={str(k): str(v) for k, v in params.items()},
        media_format=media_format,
    )


def sample_rate_from_start(start: StreamStart, default: int = EXOTEL_SAMPLE_RATE) -> int:
    """Return the PCM sample rate Exotel declared in the ``start`` media_format.

    Exotel streams 16 kHz by default and only sends 8 kHz when the applet URL
    carries ``?sample-rate=8000``, so the bridge must use the negotiated rate for
    both the LiveKit source and the audio it streams back — assuming 8 kHz when
    Exotel is actually sending 16 kHz garbles audio in both directions. Falls
    back to ``default`` when the format is absent or unrecognised.
    """
    fmt = start.media_format or {}
    raw = fmt.get("sample_rate") or fmt.get("sampleRate") or fmt.get("rate")
    try:
        rate = int(raw)
    except (TypeError, ValueError):
        return default
    return rate if rate in SUPPORTED_SAMPLE_RATES else default


def decode_media(msg: dict[str, Any]) -> bytes:
    """Return the raw PCM16/8 kHz bytes from a ``media`` event ( b"" if none)."""
    media = msg.get("media") or {}
    if not isinstance(media, dict):
        return b""
    payload = media.get("payload")
    if not payload:
        return b""
    try:
        return base64.b64decode(payload)
    except (ValueError, TypeError):
        return b""


def extract_dtmf(msg: dict[str, Any]) -> str:
    """Return the DTMF digit from a ``dtmf`` event ("" if none)."""
    dtmf = msg.get("dtmf") or {}
    if not isinstance(dtmf, dict):
        return ""
    return str(dtmf.get("digit", ""))


# ── Outbound chunking & event building ─────────────────────────────────────────

def chunk_pcm(pcm: bytes, frame_bytes: int = MIN_CHUNK_BYTES) -> Iterator[bytes]:
    """Yield Exotel-compliant PCM frames from a (possibly partial) buffer.

    Every yielded frame is a multiple of 320 bytes and within
    [MIN_CHUNK_BYTES, MAX_CHUNK_BYTES]. A trailing remainder shorter than one
    frame is NOT yielded — callers buffer it and pad/flush at end of speech via
    :func:`flush_pcm` so we never emit a non-320-multiple frame mid-stream.
    """
    frame_bytes = _normalize_frame_bytes(frame_bytes)
    total = len(pcm)
    offset = 0
    while total - offset >= frame_bytes:
        yield pcm[offset:offset + frame_bytes]
        offset += frame_bytes


def flush_pcm(pcm: bytes) -> bytes:
    """Pad a trailing remainder up to the next 320-byte multiple with silence.

    Returns b"" for an empty buffer. Used to flush the tail of an utterance so
    the final frame still satisfies Exotel's multiple-of-320 constraint.
    """
    if not pcm:
        return b""
    remainder = len(pcm) % CHUNK_MULTIPLE
    if remainder:
        pcm = pcm + b"\x00" * (CHUNK_MULTIPLE - remainder)
    return pcm


def _normalize_frame_bytes(frame_bytes: int) -> int:
    """Clamp/round a requested frame size to a legal Exotel chunk size."""
    frame_bytes = max(MIN_CHUNK_BYTES, min(MAX_CHUNK_BYTES, int(frame_bytes)))
    # Round down to a multiple of 320 (stay within the max).
    frame_bytes -= frame_bytes % CHUNK_MULTIPLE
    return max(MIN_CHUNK_BYTES, frame_bytes)


def build_media_event(stream_sid: str, pcm: bytes) -> str:
    """Build a ``media`` event (audio back to the caller) as a JSON string.

    Mirrors the minimal frame that working Exotel bidirectional clients (e.g.
    pipecat's serializer) send: camelCase ``streamSid`` and nothing but
    ``media.payload``. Exotel keys outbound playback on ``streamSid`` and rejects
    frames carrying extra fields (``chunk`` / ``sequence_number`` / ``timestamp``)
    by closing the socket after the first one, so we keep the frame minimal.
    """
    payload = base64.b64encode(pcm).decode("ascii")
    return json.dumps({
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload},
    })


def build_clear_event(stream_sid: str) -> str:
    """Build a ``clear`` event — tells Exotel to discard buffered playback.

    Sent on barge-in so the caller's interruption stops our queued audio
    immediately instead of after the already-buffered speech finishes.
    """
    return json.dumps({"event": "clear", "streamSid": stream_sid})


def build_mark_event(stream_sid: str, name: str) -> str:
    """Build a ``mark`` event — a playback checkpoint Exotel echoes back."""
    return json.dumps({
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {"name": name},
    })


__all__ = [
    "EXOTEL_SAMPLE_RATE",
    "EXOTEL_NUM_CHANNELS",
    "EXOTEL_BYTES_PER_SAMPLE",
    "EXOTEL_BYTES_PER_SEC",
    "SUPPORTED_SAMPLE_RATES",
    "CHUNK_MULTIPLE",
    "MIN_CHUNK_BYTES",
    "MAX_CHUNK_BYTES",
    "StreamStart",
    "parse_message",
    "event_type",
    "extract_stream_sid",
    "parse_start",
    "sample_rate_from_start",
    "decode_media",
    "extract_dtmf",
    "chunk_pcm",
    "flush_pcm",
    "build_media_event",
    "build_clear_event",
    "build_mark_event",
]
