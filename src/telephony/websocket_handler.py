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

from src.observability.logger import get_logger
from src.telephony.call_handler import CallHandler
from src.telephony.vad import SimpleVAD

logger = get_logger(__name__)

# Audio accumulation tuned for PCM16 @ 8 kHz (1600 bytes ≈ 100 ms)
CHUNK_DURATION_MS = 100
SILENCE_THRESHOLD_CHUNKS = 12     # 12 × 100 ms = 1.2 s of silence → end utterance
MAX_UTTERANCE_CHUNKS = 80         # 80 × 100 ms = 8 s hard cap
MIN_SPEECH_CHUNKS = 3             # require ≥ 300 ms of audio before sending to STT
OUT_FRAME_BYTES = 3200            # send outbound audio in ~200 ms chunks (multiple of 320)


async def handle_exotel_stream(
    websocket: WebSocket,
    tenant_slug: str,
) -> None:
    """Handle an Exotel Voicebot WebSocket session."""
    call_id = str(uuid.uuid4())
    stream_sid: Optional[str] = None
    handler: Optional[CallHandler] = None
    vad = SimpleVAD()

    audio_buffer = bytearray()
    silence_count = 0
    utterance_chunks = 0
    speech_chunks = 0
    system_speaking = False

    await websocket.accept()
    logger.info("ws_connected", tenant=tenant_slug)

    try:
        async for raw_msg in websocket.iter_text():
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            event = msg.get("event")

            # ── connected ──────────────────────────────────────────────────
            if event == "connected":
                logger.info("ws_event_connected", call_id=call_id)
                continue

            # ── start ──────────────────────────────────────────────────────
            if event == "start":
                start = msg.get("start", {})
                stream_sid = start.get("stream_sid") or start.get("streamSid")
                call_sid = start.get("call_sid") or start.get("callSid") or call_id
                custom = start.get("custom_parameters", {}) or {}
                caller = custom.get("from") or start.get("from")

                handler = CallHandler(
                    call_id=call_sid,
                    tenant_slug=tenant_slug,
                    caller_number=caller,
                )
                greeting_pcm = await handler.start_call()
                if greeting_pcm:
                    await _send_pcm(websocket, stream_sid, greeting_pcm)
                    system_speaking = True
                continue

            # ── dtmf ───────────────────────────────────────────────────────
            if event == "dtmf":
                logger.info("ws_dtmf", digit=msg.get("dtmf", {}).get("digit"))
                continue

            # ── media (incoming audio) ─────────────────────────────────────
            if event == "media" and handler:
                payload_b64 = msg.get("media", {}).get("payload", "")
                if not payload_b64:
                    continue
                pcm_chunk = base64.b64decode(payload_b64)

                is_speech = vad.is_speech(pcm_chunk)

                # Barge-in: caller spoke while system playing
                if system_speaking and is_speech:
                    system_speaking = False
                    await _send_clear(websocket, stream_sid)

                audio_buffer.extend(pcm_chunk)
                utterance_chunks += 1
                if is_speech:
                    speech_chunks += 1
                    silence_count = 0
                elif vad.is_silence(pcm_chunk):
                    silence_count += 1

                utterance_complete = (
                    (speech_chunks >= MIN_SPEECH_CHUNKS and silence_count >= SILENCE_THRESHOLD_CHUNKS)
                    or utterance_chunks >= MAX_UTTERANCE_CHUNKS
                )

                if utterance_complete and not system_speaking and speech_chunks > 0:
                    pcm = bytes(audio_buffer)
                    audio_buffer.clear()
                    silence_count = 0
                    utterance_chunks = 0
                    speech_chunks = 0

                    # Upsample 8 kHz → 16 kHz: Sarvam Saarika recognises
                    # speech much better at 16 kHz than 8 kHz telephony rate.
                    pcm_16k, _ = audioop.ratecv(pcm, 2, 1, 8000, 16000, None)
                    rms = audioop.rms(pcm_16k, 2)
                    logger.info("stt_input", bytes=len(pcm_16k), rms=rms)
                    wav_bytes = _pcm_to_wav(pcm_16k, sample_rate=16000)
                    response_pcm = await handler.process_audio_turn(wav_bytes)

                    if response_pcm:
                        await _send_pcm(websocket, stream_sid, response_pcm)
                        system_speaking = True
                    else:
                        system_speaking = False
                continue

            # ── stop ───────────────────────────────────────────────────────
            if event == "stop":
                logger.info("ws_event_stop", call_id=call_id)
                break

    except WebSocketDisconnect:
        logger.info("ws_disconnected", call_id=call_id)
    except Exception as e:
        logger.error("ws_error", call_id=call_id, error=str(e))
    finally:
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
    """
    if not pcm_bytes:
        return True
    if len(pcm_bytes) % 320 != 0:
        pad = 320 - (len(pcm_bytes) % 320)
        pcm_bytes = pcm_bytes + b"\x00" * pad

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
            # WS closed mid-stream — caller hung up. Stop paced send.
            return False
        await asyncio.sleep(OUT_FRAME_BYTES / (8000 * 2))    # 200 ms
    return True


async def _send_clear(websocket: WebSocket, stream_sid: Optional[str]) -> None:
    """Send clear event for barge-in (stops Exotel playback)."""
    try:
        msg = {"event": "clear", "streamSid": stream_sid}
        await websocket.send_text(json.dumps(msg))
    except Exception:
        pass


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap raw PCM16 in a WAV header so Sarvam STT can accept it."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
