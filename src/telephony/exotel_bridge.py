"""
Bridge an Exotel Voicebot/AgentStream WebSocket to a LiveKit room.

Exotel streams the caller's audio to us over a WebSocket (raw/slin 16-bit
8 kHz mono PCM, base64). Instead of running a separate STT→LLM→TTS pipeline,
we join the *same* LiveKit room the rest of the system uses and let the existing
``arya`` agent worker handle the conversation unchanged:

    Caller ──Exotel WS──► ExotelLiveKitBridge ──published track──► LiveKit room
                                                                       │
    Caller ◄─Exotel WS── ExotelLiveKitBridge ◄─agent audio track──────┘

Inbound  : room name is ``{slug}-call-{uuid}`` (same convention as the browser
           token + SIP paths) and we dispatch the agent via the join token.
Outbound : the outbound service pre-creates the room (with context metadata and
           agent dispatch) and passes its name to Exotel as a custom parameter
           ``room``; the bridge just joins it.

LiveKit is imported lazily so the rest of the app runs without the SDK
installed (mirrors ``livekit_sip.py``).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from src.config.settings import settings
from src.telephony import exotel_stream as ex

logger = structlog.get_logger(__name__)


class ExotelLiveKitBridge:
    """One instance per Exotel WebSocket connection (one call)."""

    def __init__(self, websocket: Any, slug: str) -> None:
        self._ws = websocket
        self._slug = (slug or "default").strip().lower() or "default"
        self._start: ex.StreamStart | None = None
        self._stream_sid = ""
        self._sample_rate = ex.EXOTEL_SAMPLE_RATE  # negotiated from the start event
        self._room: Any = None
        self._source: Any = None          # rtc.AudioSource (Exotel → LiveKit)
        self._forward_tasks: list[asyncio.Task] = []
        self._closed = False               # stop forwarding (peer gone or call ending)
        self._torndown = False             # idempotency guard for _teardown
        self._forwarding = False           # guard: only one agent→Exotel stream
        self._send_lock = asyncio.Lock()   # serialize WS sends (no concurrent send_text)
        # Barge-in bookkeeping: timestamp (loop time) of the last agent audio we
        # forwarded, and the last time we issued a clear, to debounce barge-ins.
        self._last_agent_audio = 0.0
        self._last_clear = 0.0
        # Diagnostics: frame counters and start time, logged at teardown so a
        # single call shows whether audio flowed each way and for how long.
        self._in_frames = 0
        self._out_frames = 0
        self._started_at = 0.0

    # ── Public entrypoint ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Read Exotel events until the call ends, bridging audio both ways."""
        try:
            while True:
                raw = await self._ws.receive_text()
                msg = ex.parse_message(raw)
                etype = ex.event_type(msg)

                if etype == "connected":
                    logger.info("exotel_ws_connected", slug=self._slug)
                elif etype == "start":
                    await self._on_start(msg)
                elif etype == "media":
                    await self._on_media(msg)
                elif etype == "dtmf":
                    digit = ex.extract_dtmf(msg)
                    logger.info("exotel_ws_dtmf", slug=self._slug, digit=digit)
                elif etype == "stop":
                    logger.info("exotel_ws_stop", slug=self._slug, sid=self._stream_sid[-6:])
                    break
                # "mark" echoes and unknown events are ignored.
        except Exception as exc:  # WebSocketDisconnect or transport error
            # A failed send marks the call closed and leaves Starlette's WS in
            # DISCONNECTED, so the next receive_text() raises a misleading
            # 'WebSocket is not connected. Need to call "accept" first.'. When we
            # already know the peer went away, report that instead of the symptom.
            reason = "peer_disconnected" if self._closed else str(exc)[:120]
            logger.info("exotel_ws_closed", slug=self._slug, reason=reason)
        finally:
            await self._teardown()

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _on_start(self, msg: dict[str, Any]) -> None:
        self._start = ex.parse_start(msg)
        self._stream_sid = self._start.stream_sid
        # Honour the rate Exotel negotiated (16 kHz unless the applet URL pins
        # ?sample-rate=8000); assuming 8 kHz when it streams 16 kHz garbles audio
        # both ways and makes the agent unintelligible to the caller.
        self._sample_rate = ex.sample_rate_from_start(self._start)
        self._started_at = asyncio.get_running_loop().time()
        # Outbound calls pre-create the room and pass its name through; inbound
        # mints a fresh room so the agent dispatches to a clean conversation.
        room_name = self._resolve_room_name()
        logger.info(
            "exotel_ws_start",
            slug=self._slug,
            room=room_name,
            sid=self._stream_sid[-6:],
            frm=self._start.from_number[-4:],
            rate=self._sample_rate,
        )
        await self._join_room(room_name)

    def _resolve_room_name(self) -> str:
        """Pick the LiveKit room for this call.

        Outbound calls pass the pre-created room via a custom parameter. Exotel
        delivers custom data under various keys (``room`` for the XML applet's
        ``<Parameter>``, or a single ``CustomField`` string for the Connect
        API), so accept an explicit ``room`` key or any value that looks like
        one of this slug's call rooms. Inbound calls mint a fresh room.
        """
        params = self._start.custom_parameters if self._start else {}
        explicit = params.get("room", "").strip()
        if explicit:
            return explicit
        prefix = f"{self._slug}-call-"
        for value in params.values():
            value = str(value).strip()
            if value.startswith(prefix):
                return value
        return f"{self._slug}-call-{uuid.uuid4().hex[:12]}"

    async def _on_media(self, msg: dict[str, Any]) -> None:
        pcm = ex.decode_media(msg)
        if not pcm or self._source is None:
            return
        await self._capture_inbound(pcm)
        self._maybe_barge_in(pcm)

    # ── LiveKit room: join + publish caller audio ──────────────────────────────

    async def _join_room(self, room_name: str) -> None:
        if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY:
            logger.error("exotel_bridge_livekit_unconfigured")
            return
        try:
            from livekit import rtc
            from livekit.api import (
                AccessToken,
                RoomAgentDispatch,
                RoomConfiguration,
                VideoGrants,
            )
        except ImportError:
            logger.error("exotel_bridge_livekit_not_installed")
            return

        identity = f"exotel-caller-{(self._start.from_number or 'x')[-4:]}"
        token = (
            AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_name("Caller")
            .with_attributes({
                "carrier": "exotel_ws",
                "from": self._start.from_number,
                "to": self._start.to_number,
                "exotel_call_sid": self._start.call_sid,
            })
            .with_grants(VideoGrants(
                room_join=True, room=room_name,
                can_publish=True, can_subscribe=True,
            ))
            .with_room_config(RoomConfiguration(
                agents=[RoomAgentDispatch(agent_name=settings.LIVEKIT_DISPATCH_NAME)],
            ))
            .to_jwt()
        )

        room = rtc.Room()
        room.on("track_subscribed", self._on_track_subscribed)
        await room.connect(
            settings.LIVEKIT_URL, token,
            options=rtc.RoomOptions(auto_subscribe=True),
        )
        self._room = room

        # Publish the caller's audio as an 8 kHz mono source. LiveKit (and the
        # agent's STT) resample as needed, so we feed Exotel's native rate
        # straight through with no manual resampling.
        source = rtc.AudioSource(self._sample_rate, ex.EXOTEL_NUM_CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track("caller", source)
        await room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        self._source = source
        logger.info("exotel_bridge_joined", room=room_name, identity=identity)

    async def _capture_inbound(self, pcm: bytes) -> None:
        """Push one Exotel media chunk into the LiveKit audio source."""
        try:
            from livekit import rtc
            # Drop a trailing odd byte: 16-bit PCM must be sample-aligned, else
            # `data` and samples_per_channel disagree and every following frame is
            # byte-shifted into noise.
            extra = len(pcm) % ex.EXOTEL_BYTES_PER_SAMPLE
            if extra:
                pcm = pcm[:-extra]
            if not pcm:
                return
            frame = rtc.AudioFrame(
                data=pcm,
                sample_rate=self._sample_rate,
                num_channels=ex.EXOTEL_NUM_CHANNELS,
                samples_per_channel=len(pcm) // ex.EXOTEL_BYTES_PER_SAMPLE,
            )
            await self._source.capture_frame(frame)
            self._in_frames += 1
        except Exception as exc:
            logger.debug("exotel_capture_failed", error=str(exc)[:100])

    # ── LiveKit → Exotel: forward agent audio back to the caller ───────────────

    def _on_track_subscribed(self, track: Any, publication: Any, participant: Any) -> None:
        """Start forwarding when the agent publishes its audio track."""
        try:
            from livekit import rtc
        except ImportError:
            return
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        # Ignore our own caller track echoed back; only forward remote (agent).
        ident = getattr(participant, "identity", "")
        if ident.startswith("exotel-caller"):
            return
        # Forward only ONE agent audio stream. track_subscribed can fire more than
        # once (re-publish / a second track); a second concurrent sender on the
        # same WebSocket corrupts its state — the receive loop then dies with
        # 'WebSocket is not connected. Need to call "accept" first.' — and doubles
        # the audio sent to the caller.
        if self._forwarding:
            return
        self._forwarding = True
        logger.info("exotel_bridge_agent_track", participant=ident)
        self._forward_tasks.append(
            asyncio.create_task(self._forward_agent_audio(track))
        )

    async def _forward_agent_audio(self, track: Any) -> None:
        """Read the agent's track at 8 kHz and stream it back to Exotel."""
        try:
            from livekit import rtc
        except ImportError:
            return
        chunk_bytes = ex._normalize_frame_bytes(settings.EXOTEL_STREAM_CHUNK_BYTES)
        # AudioStream resamples to Exotel's negotiated mono rate for us.
        audio_stream = rtc.AudioStream(
            track,
            sample_rate=self._sample_rate,
            num_channels=ex.EXOTEL_NUM_CHANNELS,
        )
        buf = bytearray()
        try:
            async for event in audio_stream:
                if self._closed:
                    break
                buf.extend(bytes(event.frame.data))
                while len(buf) >= chunk_bytes:
                    if self._closed:
                        break
                    await self._send_media(bytes(buf[:chunk_bytes]))
                    del buf[:chunk_bytes]
        except Exception as exc:
            logger.debug("exotel_forward_ended", error=str(exc)[:100])
        finally:
            # Flush the tail, padded to a legal 320-byte multiple.
            tail = ex.flush_pcm(bytes(buf))
            if tail:
                await self._send_media(tail)
            await audio_stream.aclose()

    async def _send_media(self, pcm: bytes) -> None:
        if self._closed or not self._stream_sid:
            return
        try:
            async with self._send_lock:
                await self._ws.send_text(ex.build_media_event(self._stream_sid, pcm))
            self._last_agent_audio = asyncio.get_running_loop().time()
            self._out_frames += 1
        except Exception as exc:
            # The Exotel socket is gone: Starlette flips the WS to DISCONNECTED
            # on the underlying OSError (and raises a WebSocketDisconnect we
            # swallow here). Mark the call closed so the forward loop and
            # barge-in stop writing into a dead socket; the receive side unwinds
            # on its own and teardown still runs via its own guard.
            self._closed = True
            logger.debug("exotel_send_failed", error=str(exc)[:100])

    # ── Barge-in ───────────────────────────────────────────────────────────────

    def _maybe_barge_in(self, pcm: bytes) -> None:
        """If the caller speaks while the agent is talking, flush Exotel's buffer.

        The agent's own VAD stops its speech inside LiveKit, but Exotel keeps
        playing whatever audio we already queued. A ``clear`` event drops that
        buffered playback so the interruption feels immediate.
        """
        now = asyncio.get_running_loop().time()
        # Only relevant shortly after we forwarded agent audio.
        if now - self._last_agent_audio > 1.0:
            return
        if now - self._last_clear < 0.5:   # debounce
            return
        if not self._is_speech(pcm):
            return
        self._last_clear = now
        # Track the task so it isn't garbage-collected mid-send (which silently
        # drops the clear) and is cancelled on teardown.
        self._forward_tasks.append(asyncio.create_task(self._send_clear()))

    @staticmethod
    def _is_speech(pcm: bytes) -> bool:
        """Cheap RMS energy gate to distinguish speech from line noise."""
        try:
            import numpy as np
            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return False
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            return rms > 800.0   # ~ -32 dBFS; above typical telephone noise floor
        except Exception:
            return False

    async def _send_clear(self) -> None:
        try:
            async with self._send_lock:
                await self._ws.send_text(ex.build_clear_event(self._stream_sid))
            logger.info("exotel_ws_clear_sent", sid=self._stream_sid[-6:])
        except Exception:
            self._closed = True

    # ── Teardown ───────────────────────────────────────────────────────────────

    async def _teardown(self) -> None:
        if self._torndown:
            return
        self._torndown = True
        self._closed = True
        for task in self._forward_tasks:
            task.cancel()
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:
                pass
        duration = 0.0
        if self._started_at:
            duration = round(asyncio.get_running_loop().time() - self._started_at, 1)
        logger.info(
            "exotel_bridge_torndown",
            slug=self._slug,
            sid=self._stream_sid[-6:],
            rate=self._sample_rate,
            in_frames=self._in_frames,
            out_frames=self._out_frames,
            duration_s=duration,
        )
