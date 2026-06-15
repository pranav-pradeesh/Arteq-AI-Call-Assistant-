"""
Unit tests for the Exotel Voicebot/AgentStream WebSocket serializer.

These cover the audio-format contract (raw/slin 16-bit 8 kHz mono PCM, base64)
and the 320-byte chunk constraints, with no LiveKit/network dependency.
"""
import base64
import json

from src.telephony import exotel_stream as ex


def test_audio_format_constants():
    assert ex.EXOTEL_SAMPLE_RATE == 8000
    assert ex.EXOTEL_NUM_CHANNELS == 1
    assert ex.EXOTEL_BYTES_PER_SAMPLE == 2
    assert ex.EXOTEL_BYTES_PER_SEC == 16000  # 8000 Hz * 2 bytes * mono
    # 100 ms of audio = 1600 bytes, but Exotel's min frame is 3200 bytes.
    assert ex.MIN_CHUNK_BYTES == 3200
    assert ex.MIN_CHUNK_BYTES % ex.CHUNK_MULTIPLE == 0
    assert ex.MAX_CHUNK_BYTES == 100000


def test_parse_start_snake_case():
    msg = {
        "event": "start",
        "stream_sid": "MZ123",
        "start": {
            "stream_sid": "MZ123",
            "call_sid": "CA9",
            "from": "+919876543210",
            "to": "+918047491899",
            "custom_parameters": {"room": "kairali-call-abc", "to": "+918047491899"},
            "media_format": {"encoding": "slin", "sample_rate": "8000"},
        },
    }
    start = ex.parse_start(msg)
    assert start.stream_sid == "MZ123"
    assert start.call_sid == "CA9"
    assert start.from_number == "+919876543210"
    assert start.custom_parameters["room"] == "kairali-call-abc"
    assert start.media_format["encoding"] == "slin"


def test_parse_start_camel_case_fallback():
    msg = {
        "event": "start",
        "streamSid": "MZ999",
        "start": {"streamSid": "MZ999", "callSid": "CA1", "customParameters": {"k": "v"}},
    }
    start = ex.parse_start(msg)
    assert start.stream_sid == "MZ999"
    assert start.call_sid == "CA1"
    assert start.custom_parameters == {"k": "v"}


def test_extract_stream_sid_variants():
    assert ex.extract_stream_sid({"stream_sid": "A"}) == "A"
    assert ex.extract_stream_sid({"streamSid": "B"}) == "B"
    assert ex.extract_stream_sid({"start": {"stream_sid": "C"}}) == "C"
    assert ex.extract_stream_sid({"event": "media"}) == ""


def test_decode_media_roundtrip():
    pcm = bytes(range(256)) * 4  # 1024 bytes of pseudo audio
    msg = {"event": "media", "media": {"payload": base64.b64encode(pcm).decode()}}
    assert ex.decode_media(msg) == pcm


def test_decode_media_missing_payload():
    assert ex.decode_media({"event": "media", "media": {}}) == b""
    assert ex.decode_media({"event": "stop"}) == b""


def test_extract_dtmf():
    assert ex.extract_dtmf({"event": "dtmf", "dtmf": {"digit": "5"}}) == "5"
    assert ex.extract_dtmf({"event": "media"}) == ""


def test_chunk_pcm_yields_only_full_legal_frames():
    # 8000 bytes -> two 3200-byte frames, 1600-byte remainder withheld.
    pcm = b"\x01\x02" * 4000  # 8000 bytes
    frames = list(ex.chunk_pcm(pcm))
    assert len(frames) == 2
    for f in frames:
        assert len(f) == ex.MIN_CHUNK_BYTES
        assert len(f) % ex.CHUNK_MULTIPLE == 0
    consumed = len(frames) * ex.MIN_CHUNK_BYTES
    assert len(pcm) - consumed == 1600  # remainder left for flush


def test_chunk_pcm_custom_frame_size_normalized():
    # Request a non-multiple, too-small size — it must be clamped to a legal one.
    pcm = b"\x00" * 20000
    frames = list(ex.chunk_pcm(pcm, frame_bytes=337))
    assert frames, "expected at least one frame"
    for f in frames:
        assert len(f) % ex.CHUNK_MULTIPLE == 0
        assert ex.MIN_CHUNK_BYTES <= len(f) <= ex.MAX_CHUNK_BYTES


def test_flush_pcm_pads_to_multiple():
    out = ex.flush_pcm(b"\x01" * 1000)  # 1000 not a multiple of 320
    assert len(out) % ex.CHUNK_MULTIPLE == 0
    assert len(out) == 1280  # next multiple of 320 above 1000
    assert out[:1000] == b"\x01" * 1000
    assert out[1000:] == b"\x00" * 280
    assert ex.flush_pcm(b"") == b""


def test_build_media_event_shape():
    pcm = b"\xab\xcd" * 16
    raw = ex.build_media_event("MZ1", pcm, chunk=3, seq=3)
    msg = json.loads(raw)
    assert msg["event"] == "media"
    assert msg["stream_sid"] == "MZ1"
    assert msg["media"]["chunk"] == 3
    assert msg["sequence_number"] == 3
    assert base64.b64decode(msg["media"]["payload"]) == pcm


def test_build_clear_event_shape():
    msg = json.loads(ex.build_clear_event("MZ2"))
    assert msg == {"event": "clear", "stream_sid": "MZ2"}


def test_build_mark_event_shape():
    msg = json.loads(ex.build_mark_event("MZ3", "greeting-done", seq=7))
    assert msg["event"] == "mark"
    assert msg["mark"]["name"] == "greeting-done"
    assert msg["sequence_number"] == 7


def test_parse_message_malformed():
    assert ex.parse_message("not json") == {}
    assert ex.parse_message(b'{"event": "connected"}') == {"event": "connected"}
    assert ex.event_type({"event": "START"}) == "start"
