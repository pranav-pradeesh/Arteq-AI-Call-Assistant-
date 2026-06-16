"""
Tests for ExotelLiveKitBridge teardown / disconnect handling.

These focus on the bridge's WebSocket lifecycle — specifically that a send
failure (the Exotel socket going away mid-call) is handled cleanly instead of
surfacing later as the misleading 'WebSocket is not connected. Need to call
"accept" first.' RuntimeError from the receive loop.

The bridge imports LiveKit lazily inside its methods, so the class can be
constructed and these paths exercised without the SDK installed.
"""
from __future__ import annotations

import asyncio

from src.telephony.exotel_bridge import ExotelLiveKitBridge


class FakeWS:
    """Minimal stand-in for a Starlette WebSocket.

    ``send_text`` optionally raises to mimic a dead socket; ``receive_text``
    yields a queued script of return values / exceptions.
    """

    def __init__(self, *, send_raises: Exception | None = None, recv_script=None) -> None:
        self._send_raises = send_raises
        self._recv_script = list(recv_script or [])
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        if self._send_raises is not None:
            raise self._send_raises
        self.sent.append(data)

    async def receive_text(self) -> str:
        if not self._recv_script:
            raise AssertionError("receive_text called more times than scripted")
        item = self._recv_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_send_media_failure_marks_closed():
    """A failed send flips _closed (so forwarding stops) but does not raise."""
    ws = FakeWS(send_raises=OSError("broken pipe"))
    bridge = ExotelLiveKitBridge(ws, "default")
    bridge._stream_sid = "abc123"

    asyncio.run(bridge._send_media(b"\x00\x00" * 1600))

    assert bridge._closed is True
    assert ws.sent == []  # nothing made it out


def test_send_clear_failure_marks_closed():
    ws = FakeWS(send_raises=OSError("broken pipe"))
    bridge = ExotelLiveKitBridge(ws, "default")
    bridge._stream_sid = "abc123"

    asyncio.run(bridge._send_clear())

    assert bridge._closed is True


def test_send_media_noop_when_closed():
    """Once closed, sends short-circuit without touching the socket."""
    ws = FakeWS()
    bridge = ExotelLiveKitBridge(ws, "default")
    bridge._stream_sid = "abc123"
    bridge._closed = True

    asyncio.run(bridge._send_media(b"\x00\x00" * 1600))

    assert ws.sent == []


def test_run_reports_peer_disconnected_not_accept_error(monkeypatch):
    """When a prior send marked the call closed, the receive loop's RuntimeError
    is reported as 'peer_disconnected' rather than the misleading accept error."""
    records: list[tuple[str, dict]] = []

    class RecLogger:
        def info(self, event, **kw):
            records.append((event, kw))

        def debug(self, event, **kw):
            records.append((event, kw))

        def error(self, event, **kw):
            records.append((event, kw))

    monkeypatch.setattr("src.telephony.exotel_bridge.logger", RecLogger())

    # Simulate: a send already detected the peer is gone (_closed True), then the
    # next receive_text raises the symptom RuntimeError Starlette produces.
    boom = RuntimeError('WebSocket is not connected. Need to call "accept" first.')
    ws = FakeWS(recv_script=[boom])
    bridge = ExotelLiveKitBridge(ws, "default")
    bridge._closed = True

    asyncio.run(bridge.run())

    closed = [kw for ev, kw in records if ev == "exotel_ws_closed"]
    assert closed, "expected an exotel_ws_closed log"
    assert closed[0]["reason"] == "peer_disconnected"
    # Teardown still runs even though _closed was already set.
    assert any(ev == "exotel_bridge_torndown" for ev, _ in records)
    assert bridge._torndown is True


def test_run_reports_real_reason_when_not_closed(monkeypatch):
    """A disconnect that wasn't preceded by a send failure keeps its real reason."""
    records: list[tuple[str, dict]] = []

    class RecLogger:
        def info(self, event, **kw):
            records.append((event, kw))

        def debug(self, event, **kw):
            records.append((event, kw))

        def error(self, event, **kw):
            records.append((event, kw))

    monkeypatch.setattr("src.telephony.exotel_bridge.logger", RecLogger())

    ws = FakeWS(recv_script=[RuntimeError("some transport error")])
    bridge = ExotelLiveKitBridge(ws, "default")

    asyncio.run(bridge.run())

    closed = [kw for ev, kw in records if ev == "exotel_ws_closed"]
    assert closed and closed[0]["reason"] == "some transport error"
