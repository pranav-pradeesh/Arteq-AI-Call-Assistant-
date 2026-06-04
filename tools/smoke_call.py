#!/usr/bin/env python3
"""
Headless smoke-call — proves the live voice loop end-to-end without a human mic.

What it does, self-contained:
  1. starts the LiveKit agent worker (Arya) as a subprocess and waits for it to
     register with LiveKit Cloud,
  2. mints a join token with RoomAgentDispatch(agent_name="arya") — same path
     the browser /talk page uses,
  3. joins the room headless via livekit.rtc,
  4. waits for the agent participant to be dispatched + connected,
  5. subscribes to the agent's audio track and counts decoded frames (proves
     STT/LLM/TTS produced a spoken greeting),
  6. captures any transcription events,
  7. logs a PASS/FAIL report to console and to ``arteq-smokecall.log``.

Run:
    python run.py smoke-call            # any platform (sets up venv first)
    python tools/smoke_call.py          # directly, inside the venv

PASS = agent joined AND audio frames received within the timeout.
This NEVER speaks into the mic; it verifies the agent connects and talks back.
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Malayalam transcripts must print without crashing on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_buf = io.StringIO()

# How long to wait for worker registration and for the agent to join + speak.
WORKER_REGISTER_TIMEOUT = 40
AGENT_JOIN_TIMEOUT = 35
AUDIO_LISTEN_SECONDS = 12


def _emit(line: str = "") -> None:
    try:
        print(line, flush=True)
    except Exception:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()
    _buf.write(line + "\n")


def _flush_log() -> None:
    try:
        (ROOT / "arteq-smokecall.log").write_text(_buf.getvalue(), encoding="utf-8")
        _emit(f"\n  Full report written to: {ROOT / 'arteq-smokecall.log'}")
    except Exception:
        pass


def _mint_token(slug: str) -> tuple[str, str, str]:
    """Mint a join token with agent dispatch. Returns (token, room, url)."""
    from src.config.settings import settings
    from livekit.api import (
        AccessToken, VideoGrants, RoomConfiguration, RoomAgentDispatch,
    )
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise RuntimeError("LIVEKIT_API_KEY/SECRET not set in .env")
    if not settings.LIVEKIT_URL:
        raise RuntimeError("LIVEKIT_URL not set in .env")

    room_name = f"{slug}-call-{uuid.uuid4().hex[:12]}"
    token = (
        AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity("smoke-tester")
        .with_name("smoke-tester")
        .with_grants(VideoGrants(
            room_join=True, room=room_name,
            can_publish=True, can_subscribe=True,
        ))
        .with_room_config(RoomConfiguration(
            agents=[RoomAgentDispatch(agent_name="arya")],
        ))
        .to_jwt()
    )
    return token, room_name, settings.LIVEKIT_URL


def _start_worker() -> subprocess.Popen:
    """Launch the agent worker and wait until it registers with LiveKit Cloud."""
    py = sys.executable
    log_path = ROOT / "arteq-worker.log"
    logf = open(log_path, "w", encoding="utf-8")
    _emit(f"  Starting agent worker (logs -> {log_path}) ...")
    proc = subprocess.Popen(
        [py, str(ROOT / "livekit_agent.py"), "dev"],
        cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + WORKER_REGISTER_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"worker exited early (code {proc.returncode}) — see {log_path}")
        try:
            txt = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            txt = ""
        if "registered worker" in txt or "worker_id" in txt or "registered" in txt.lower():
            _emit("  Worker registered with LiveKit Cloud.")
            return proc
        time.sleep(1)
    _emit("  [WARN] Did not see explicit 'registered' log; proceeding anyway.")
    return proc


async def _run_call(slug: str) -> bool:
    from livekit import rtc

    token, room_name, url = _mint_token(slug)
    _emit(f"  Room: {room_name}")
    _emit(f"  LiveKit URL: {url}")

    room = rtc.Room()
    agent_joined = asyncio.Event()
    audio_frames = {"count": 0}
    transcripts: list[str] = []

    @room.on("participant_connected")
    def _on_participant(p):
        _emit(f"  participant joined: identity={p.identity}")
        agent_joined.set()

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        _emit(f"  track subscribed: kind={track.kind} from={participant.identity}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(_drain_audio(track, audio_frames))

    def _on_transcript(segments, *args):
        for seg in segments:
            text = getattr(seg, "text", "") or ""
            if text:
                transcripts.append(text)
                _emit(f"  transcript: {text!r}")

    # Transcription event name differs across SDK versions; register defensively.
    for ev in ("transcription_received", "transcription"):
        try:
            room.on(ev, _on_transcript)
        except Exception:
            pass

    await room.connect(url, token)
    _emit("  Joined room. Waiting for agent dispatch ...")

    # Agent may already be present (connected before our handler) — check roster.
    if room.remote_participants:
        agent_joined.set()

    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=AGENT_JOIN_TIMEOUT)
        _emit("  Agent participant present.")
    except asyncio.TimeoutError:
        _emit(f"  [FAIL] No agent joined within {AGENT_JOIN_TIMEOUT}s.")
        await room.disconnect()
        return False

    _emit(f"  Listening {AUDIO_LISTEN_SECONDS}s for agent audio (greeting) ...")
    await asyncio.sleep(AUDIO_LISTEN_SECONDS)
    await room.disconnect()

    _emit("")
    _emit(f"  audio frames received: {audio_frames['count']}")
    _emit(f"  transcripts captured:  {len(transcripts)}")
    ok = audio_frames["count"] > 0
    return ok


async def _drain_audio(track, counter: dict) -> None:
    from livekit import rtc
    stream = rtc.AudioStream(track)
    try:
        async for _ev in stream:
            counter["count"] += 1
    except Exception:
        pass


async def _main_async(slug: str) -> int:
    _emit("=" * 64)
    _emit("  Arteq — headless smoke-call (live voice loop)")
    _emit("=" * 64)

    worker = None
    try:
        worker = _start_worker()
        ok = await _run_call(slug)
    except Exception as e:
        _emit(f"  [FAIL] {e}")
        ok = False
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=8)
            except Exception:
                worker.kill()
        try:
            from src.db.queries import close_pool
            await close_pool()
        except Exception:
            pass

    _emit("")
    _emit("Summary")
    _emit("-------")
    if ok:
        _emit("  [PASS] Agent dispatched, joined, and produced audio. Live loop works.")
    else:
        _emit("  [FAIL] Live loop did not complete. Check arteq-worker.log for the")
        _emit("         agent-side error (STT/LLM/TTS keys, LiveKit dispatch, etc.).")
    _flush_log()
    return 0 if ok else 1


def main() -> int:
    slug = "default"
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        slug = sys.argv[1].strip().lower()
    return asyncio.run(_main_async(slug))


if __name__ == "__main__":
    sys.exit(main())
