"""
WebSocket handler for Exotel's Voicebot/Stream applet.

Protocol (per Exotel docs):
  Incoming events from Exotel:
    {"event":"connected"}
    {"event":"start","start":{"stream_sid":"MZ...","call_sid":"...","custom_parameters":{}}}
    {"event":"media","media":{"payload":"<base64 PCM16 8kHz mono LE>","chunk":"1","timestamp":"..."}}
    {"event":"dtmf","dtmf":{"digit":"1"}}
    {"event":"stop","stop":{...}}

  Outgoing events from bot (must include streamSid camelCase):
    {"event":"media","streamSid":"MZ...","media":{"payload":"<base64 PCM>"}}
    {"event":"clear","streamSid":"MZ..."}     # barge-in / interrupt playback
    {"event":"mark","streamSid":"MZ...","mark":{"name":"..."}}

Audio format:
  Raw/slin: 16-bit PCM, 8 kHz, mono, little-endian, base64-encoded.
  Frame size ~100 ms (1600 bytes @ 8 kHz PCM16), must be a multiple of 320.

Barge-in:
  TTS playback runs as a background task. While it's running we keep
  scoring incoming chunks. When the caller produces ≥ BARGEIN_SPEECH_CHUNKS
  of speech we cancel the playback task, send a "clear" to flush
  Exotel's buffer, and start fresh utterance capture.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import uuid
import wave
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from src.config.settings import settings
from src.observability.logger import get_logger
from src.telephony.call_handler import CallHandler
from src.telephony.vad import SimpleVAD

logger = get_logger(__name__)


async def _resolve_hospital_id(tenant_slug: str) -> str:
    """Map tenant_slug to a hospital_id.

    Strategy:
      1. If slug looks like a UUID, use it directly.
      2. Otherwise, look up the hospitals table for a matching id prefix or name slug.
      3. Fallback to settings.HOSPITAL_ID.
    """
    import re
    UUID_RE = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
    )
    if UUID_RE.match(tenant_slug):
        return tenant_slug
    try:
        from src.db.queries import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM hospitals WHERE active=true "
                "AND (LOWER(REPLACE(name,' ','-'))=$1 OR id::text=$1) LIMIT 1",
                tenant_slug.lower(),
            )
            if row:
                return str(row["id"])
    except Exception as e:
        logger.warning("tenant_slug_lookup_failed", slug=tenant_slug, error=str(e))
    return settings.HOSPITAL_ID


# Audio tuning (PCM16 @ 8 kHz, 1600 bytes ≈ 100 ms)
CHUNK_DURATION_MS = 100
SILENCE_THRESHOLD_CHUNKS = 12     # 1.2 s of silence ends an utterance
MAX_UTTERANCE_CHUNKS = 80         # 8 s hard cap
MIN_SPEECH_CHUNKS = 2             # need 200 ms of speech before STT
BARGEIN_SPEECH_CHUNKS = 2         # 200 ms of speech during TTS triggers barge-in
BARGEIN_RMS = 600                 # higher threshold during playback to ignore echo
OUT_FRAME_BYTES = 3200            # ~200 ms outbound chunks (multiple of 320)


async def handle_exotel_stream(
    websocket: WebSocket,
    tenant_slug: str,
) -> None:
    """Handle an Exotel Voicebot WebSocket session with barge-in support."""
    hospital_id = await _resolve_hospital_id(tenant_slug)
    call_id = str(uuid.uuid4())
    stream_sid: Optional[str] = None
    handler: Optional[CallHandler] = None
    vad = SimpleVAD()

    audio_buffer = bytearray()
    silence_count = 0
    utterance_chunks = 0
    speech_chunks = 0
    bargein_speech_chunks = 0
    playback_task: Optional[asyncio.Task] = None

    def is_speaking() -> bool:
        return playback_task is not None and not playback_task.done()

    def start_playback(pcm: bytes) -> None:
        nonlocal playback_task
        if playback_task and not playback_task.done():
            playback_task.cancel()
        playback_task = asyncio.create_task(
            _send_pcm(websocket, stream_sid, pcm)
        )

    async def interrupt_playback() -> None:
        """Cancel the in-flight TTS stream and flush Exotel's playback queue."""
        nonlocal playback_task
        if playback_task and not playback_task.done():
            playback_task.cancel()
            try:
                await playback_task
            except (asyncio.CancelledError, Exception):
                pass
        await _send_clear(websocket, stream_sid)
        playback_task = None
        logger.info("bargein_triggered", call_id=call_id)

    await websocket.accept()
    logger.info("ws_connected", tenant=tenant_slug)

    try:
        async for raw_msg in websocket.iter_text():
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue
            event = msg.get("event")

            if event == "connected":
                logger.info("ws_event_connected", call_id=call_id)
                continue

            if event == "start":
                start = msg.get("start", {})
                stream_sid = start.get("stream_sid") or start.get("streamSid")
                call_sid = start.get("call_sid") or start.get("callSid") or call_id
                custom = start.get("custom_parameters", {}) or {}
                caller = custom.get("from") or start.get("from")

                handler = CallHandler(
                    call_id=call_sid,
                    hospital_id=hospital_id,
                    caller_number=caller,
                )
                greeting_pcm = await handler.start_call()
                if greeting_pcm:
                    start_playback(greeting_pcm)
                continue

            if event == "dtmf":
                logger.info("ws_dtmf", digit=msg.get("dtmf", {}).get("digit"))
                continue

            if event == "stop":
                logger.info("ws_event_stop", call_id=call_id)
                break

            if event != "media" or handler is None:
                continue

            # ── Incoming audio ────────────────────────────────────────────
            payload_b64 = msg.get("media", {}).get("payload", "")
            if not payload_b64:
                continue
            try:
                pcm_chunk = base64.b64decode(payload_b64)
            except Exception:
                continue
            rms = vad.rms_energy(pcm_chunk)
            is_speech_chunk = rms > vad.speech_threshold

            # ── Barge-in detection (while playing) ────────────────────────
            if is_speaking():
                # Use a tighter threshold during playback to ignore echo/PC noise
                if rms > BARGEIN_RMS:
                    bargein_speech_chunks += 1
                else:
                    bargein_speech_chunks = 0

                if bargein_speech_chunks >= BARGEIN_SPEECH_CHUNKS:
                    await interrupt_playback()
                    bargein_speech_chunks = 0
                    # Seed the new utterance with the chunk that triggered barge-in
                    audio_buffer.clear()
                    audio_buffer.extend(pcm_chunk)
                    silence_count = 0
                    utterance_chunks = 1
                    speech_chunks = 1
                continue

            # ── Accumulate when bot is silent ─────────────────────────────
            bargein_speech_chunks = 0
            audio_buffer.extend(pcm_chunk)
            utterance_chunks += 1
            if is_speech_chunk:
                speech_chunks += 1
                silence_count = 0
            elif vad.is_silence(pcm_chunk):
                silence_count += 1

            utterance_complete = (
                (speech_chunks >= MIN_SPEECH_CHUNKS and silence_count >= SILENCE_THRESHOLD_CHUNKS)
                or utterance_chunks >= MAX_UTTERANCE_CHUNKS
            )

            if utterance_complete and speech_chunks > 0:
                pcm = bytes(audio_buffer)
                audio_buffer.clear()
                silence_count = 0
                utterance_chunks = 0
                speech_chunks = 0

                # Upsample 8 kHz → 16 kHz for Saarika; STT is more accurate at 16k.
                pcm_16k, _ = audioop.ratecv(pcm, 2, 1, 8000, 16000, None)
                logger.info("stt_input", bytes=len(pcm_16k), rms=audioop.rms(pcm_16k, 2))
                wav_bytes = _pcm_to_wav(pcm_16k, sample_rate=16000)
                response_pcm = await handler.process_audio_turn(wav_bytes)

                if response_pcm:
                    start_playback(response_pcm)

    except WebSocketDisconnect:
        logger.info("ws_disconnected", call_id=call_id)
    except Exception as e:
        logger.error("ws_error", call_id=call_id, error=str(e))
    finally:
        if playback_task and not playback_task.done():
            playback_task.cancel()
        if handler:
            await handler.end_call()
        logger.info("call_cleanup_done", call_id=call_id)


async def handle_twilio_stream(websocket: WebSocket, tenant_slug: str) -> None:
    """Twilio uses near-identical protocol; reuse Exotel handler."""
    await handle_exotel_stream(websocket, tenant_slug)


# ── Outbound helpers ──────────────────────────────────────────────────────────

async def _send_pcm(
    websocket: WebSocket,
    stream_sid: Optional[str],
    pcm_bytes: bytes,
) -> bool:
    """
    Send raw PCM16 8kHz mono to Exotel as base64 in 200ms frames.
    Returns False if the socket closed mid-stream (so the caller stops).

    Cancellable: if asyncio.CancelledError is raised (barge-in), we stop
    cleanly without trying further sends.
    """
    if not pcm_bytes:
        return True
    if len(pcm_bytes) % 320 != 0:
        pad = 320 - (len(pcm_bytes) % 320)
        pcm_bytes = pcm_bytes + b"\x00" * pad

    try:
        for i in range(0, len(pcm_bytes), OUT_FRAME_BYTES):
            frame = pcm_bytes[i : i + OUT_FRAME_BYTES]
            msg = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": base64.b64encode(frame).decode("ascii")},
            }
            try:
                await websocket.send_text(json.dumps(msg))
            except Exception:
                return False
            await asyncio.sleep(OUT_FRAME_BYTES / (8000 * 2))    # 200 ms
    except asyncio.CancelledError:
        # Barge-in cancelled this stream — propagate so the awaiter knows.
        raise
    return True


async def _send_clear(websocket: WebSocket, stream_sid: Optional[str]) -> None:
    """Tell Exotel to flush its playback buffer (barge-in)."""
    try:
        msg = {"event": "clear", "streamSid": stream_sid}
        await websocket.send_text(json.dumps(msg))
    except Exception:
        pass


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap raw PCM16 in a WAV header for Sarvam STT."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
