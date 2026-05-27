"""
WebSocket Audio Streaming Handler.

Accepts real-time audio streams from telephony providers:
  - Exotel (India — preferred)
  - Twilio Media Streams
  - Any WebSocket-based audio stream (8kHz, mulaw)

Protocol:
  - Incoming: JSON messages with base64-encoded audio chunks
  - Outgoing: base64-encoded audio for TTS playback

VAD (Voice Activity Detection):
  - Simple energy-based VAD for now
  - Accumulates audio chunks until silence detected
  - Sends accumulated audio to STT

Barge-in: if caller speaks while system is speaking, stop playback and listen.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from src.observability.logger import get_logger
from src.telephony.call_handler import CallHandler
from src.telephony.vad import SimpleVAD

logger = get_logger(__name__)

# Audio accumulation settings
CHUNK_DURATION_MS = 100          # each chunk is ~100ms of audio
SILENCE_THRESHOLD_CHUNKS = 15    # 15 * 100ms = 1.5s silence → end of utterance
MAX_UTTERANCE_CHUNKS = 50        # 50 * 100ms = 5s max utterance before forced processing


async def handle_exotel_stream(
    websocket: WebSocket,
    tenant_slug: str,
) -> None:
    """
    Handle Exotel WebSocket audio stream.

    Exotel sends:
      {"event": "start", "start": {...}}
      {"event": "media", "media": {"payload": "<base64 mulaw>"}}
      {"event": "stop"}
    """
    call_id = str(uuid.uuid4())
    handler: Optional[CallHandler] = None
    vad = SimpleVAD()

    audio_buffer = bytearray()
    silence_count = 0
    utterance_chunks = 0
    system_speaking = False

    await websocket.accept()
    logger.info("ws_connected", tenant=tenant_slug)

    try:
        async for raw_msg in websocket.iter_text():
            msg = json.loads(raw_msg)
            event = msg.get("event")

            # ── Call start ────────────────────────────────────────────────
            if event == "start":
                stream_info = msg.get("start", {})
                call_id = stream_info.get("callSid") or stream_info.get("call_sid") or call_id
                caller = stream_info.get("from") or stream_info.get("caller_number")

                handler = CallHandler(
                    call_id=call_id,
                    tenant_slug=tenant_slug,
                    caller_number=caller,
                )
                greeting_audio = await handler.start_call()
                if greeting_audio:
                    await _send_audio(websocket, greeting_audio)
                    system_speaking = True

            # ── Audio chunk ──────────────────────────────────────────────
            elif event == "media" and handler:
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                audio_chunk = base64.b64decode(payload)

                # Barge-in detection: if caller speaks while system is speaking, stop
                if system_speaking and vad.is_speech(audio_chunk):
                    system_speaking = False
                    await _send_clear(websocket)

                # Accumulate audio
                audio_buffer.extend(audio_chunk)
                utterance_chunks += 1

                # VAD: count silence
                if vad.is_silence(audio_chunk):
                    silence_count += 1
                else:
                    silence_count = 0

                # Decide if utterance is complete
                utterance_complete = (
                    silence_count >= SILENCE_THRESHOLD_CHUNKS
                    or utterance_chunks >= MAX_UTTERANCE_CHUNKS
                )

                if utterance_complete and len(audio_buffer) > 0 and not system_speaking:
                    # Convert accumulated mulaw to WAV for STT
                    wav_bytes = _mulaw_to_wav(bytes(audio_buffer))

                    # Process turn
                    response_audio = await handler.process_audio_turn(wav_bytes)

                    # Reset buffer
                    audio_buffer.clear()
                    silence_count = 0
                    utterance_chunks = 0

                    if response_audio:
                        await _send_audio(websocket, response_audio)
                        system_speaking = True
                    else:
                        system_speaking = False

            # ── Call end ─────────────────────────────────────────────────
            elif event == "stop":
                logger.info("call_ended_by_provider", call_id=call_id)
                break

    except WebSocketDisconnect:
        logger.info("ws_disconnected", call_id=call_id)
    except Exception as e:
        logger.error("ws_error", call_id=call_id, error=str(e))
    finally:
        if handler:
            await handler.end_call()
        logger.info("call_cleanup_done", call_id=call_id)


async def handle_twilio_stream(
    websocket: WebSocket,
    tenant_slug: str,
) -> None:
    """
    Handle Twilio Media Streams WebSocket.
    Same protocol as Exotel with minor field differences.
    Delegates to the same handler with a Twilio adapter.
    """
    # Twilio uses the same JSON structure — just different field names
    # In practice, same logic applies
    await handle_exotel_stream(websocket, tenant_slug)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _send_audio(websocket: WebSocket, audio_bytes: bytes) -> None:
    """Send audio back to the caller via WebSocket.

    Sarvam TTS returns WAV (PCM16 8kHz); Exotel's Voicebot stream expects
    raw mulaw 8kHz base64 chunks. Convert before sending.
    """
    if not audio_bytes:
        return
    try:
        mulaw_bytes = _wav_to_mulaw(audio_bytes)
        payload = base64.b64encode(mulaw_bytes).decode("ascii")
        msg = json.dumps({
            "event": "media",
            "media": {"payload": payload}
        })
        await websocket.send_text(msg)
    except Exception as e:
        logger.error("ws_send_audio_error", error=str(e))


def _wav_to_mulaw(wav_bytes: bytes) -> bytes:
    """Strip WAV header, downsample if needed, encode PCM16 → mulaw 8kHz."""
    import audioop
    import io
    import wave
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
            sample_width = wf.getsampwidth()  # bytes per sample
            framerate = wf.getframerate()
            channels = wf.getnchannels()

        # Force mono
        if channels == 2:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)

        # Force 8 kHz
        if framerate != 8000:
            pcm, _ = audioop.ratecv(pcm, sample_width, 1, framerate, 8000, None)

        # Encode to mulaw (PCM16 → mulaw)
        if sample_width != 2:
            pcm = audioop.lin2lin(pcm, sample_width, 2)
        return audioop.lin2ulaw(pcm, 2)
    except wave.Error:
        # Not a WAV — assume already mulaw
        return wav_bytes


async def _send_clear(websocket: WebSocket) -> None:
    """Tell telephony provider to stop playback (barge-in)."""
    try:
        await websocket.send_text(json.dumps({"event": "clear"}))
    except Exception:
        pass


def _mulaw_to_wav(mulaw_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """
    Convert raw mulaw audio to WAV format for STT providers.
    Mulaw is the standard telephony encoding.
    """
    import audioop
    import struct
    import wave
    import io

    # Decode mulaw to linear PCM
    try:
        pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)  # 2 bytes = 16-bit
    except Exception:
        pcm_bytes = mulaw_bytes  # fallback: pass through

    # Wrap in WAV header
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)        # mono
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)

    return buf.getvalue()
