"""
LiveKit SIP integration — Plivo as the telephony carrier.

Call flows
----------
Inbound (patient calls hospital):
  Patient → Plivo DID
  → Plivo POST /api/v1/call/inbound/{slug}
  → PCML <Dial><User>sip:{did}@{livekit_sip_host}</User></Dial>
  → LiveKit SIP inbound trunk (matched by DID)
  → Dispatch rule creates room "{slug}-call-{uuid}"
  → Agent worker auto-dispatches to room

Outbound (reminders / confirmations / callbacks / follow-ups):
  Scheduler calls dial_outbound()
  → LiveKit room created with outbound context in metadata
  → create_sip_participant: LiveKit dials patient via Plivo SIP outbound trunk
  → Patient answers → audio flows through LiveKit room
  → Agent worker auto-dispatches to room, reads context from room.metadata

One-time setup (run POST /admin/sip/setup after first deploy):
  • Creates Plivo SIP outbound trunk in LiveKit
  • For each hospital with a provisioned DID: creates inbound trunk + dispatch rule
  • Returns trunk IDs — save LIVEKIT_SIP_OUTBOUND_TRUNK_ID to Render env vars
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

# Plivo sends SIP calls from these CIDR ranges (update if Plivo adds new ranges)
_PLIVO_SIP_CIDRS = [
    "67.231.8.0/21",
    "67.231.16.0/21",
    "172.99.68.0/22",
    "172.99.128.0/18",
    "54.172.60.0/30",
    "52.66.40.0/24",
]

# Exotel SIP CIDRs (India region). Update if Exotel adds ranges.
_EXOTEL_SIP_CIDRS = [
    "52.74.56.0/24",
    "13.251.52.0/24",
    "139.59.48.0/20",
    "13.127.0.0/16",
    "52.66.0.0/16",
]


def _lk():
    """Return a LiveKit API client. Raises if livekit package not installed."""
    try:
        from livekit import api as lk_api
        return lk_api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    except ImportError as exc:
        raise RuntimeError("livekit package not installed — pip install livekit") from exc


async def delete_room(room_name: str) -> bool:
    """Tear down a room, disconnecting every participant — including the SIP leg,
    which hangs up the phone call. Used to end a call after Arya says goodbye."""
    if not room_name:
        return False
    try:
        from livekit import api as lk_api
        lk = _lk()
        try:
            await lk.room.delete_room(lk_api.DeleteRoomRequest(room=room_name))
        finally:
            await lk.aclose()
        logger.info("room_deleted_hangup", room=room_name)
        return True
    except Exception as exc:
        logger.warning("room_delete_failed", room=room_name, error=str(exc))
        return False


# ── One-time provisioning ──────────────────────────────────────────────────────

async def setup_sip_outbound_trunk_exotel() -> str:
    """
    Create Exotel SIP outbound trunk in LiveKit.
    Returns the trunk ID — set it as LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID in env.

    Exotel SIP hostname: sip.exotel.com (or region-specific; check Exotel dashboard).
    Credentials: use EXOTEL_API_KEY + EXOTEL_API_TOKEN as SIP auth.
    """
    try:
        from livekit import api as lk_api
        lk = _lk()
        trunk = await lk.sip.create_sip_outbound_trunk(
            lk_api.CreateSIPOutboundTrunkRequest(
                trunk=lk_api.SIPOutboundTrunkInfo(
                    name="Exotel Outbound",
                    address="sip.exotel.com",
                    numbers=[settings.EXOTEL_PHONE_NUMBER],
                    auth_username=settings.EXOTEL_API_KEY,
                    auth_password=settings.EXOTEL_API_TOKEN,
                    transport=lk_api.SIPTransport.SIP_TRANSPORT_TLS,
                )
            )
        )
        await lk.aclose()
        logger.info("sip_exotel_outbound_trunk_created", trunk_id=trunk.sip_trunk_id)
        return trunk.sip_trunk_id
    except Exception as exc:
        logger.error("sip_exotel_outbound_trunk_failed", error=str(exc))
        return ""


async def setup_hospital_inbound_exotel(
    hospital_slug: str,
    did_number: str,
) -> tuple[str, str]:
    """
    Create a SIP inbound trunk + dispatch rule for one Exotel ExoPhone.
    Returns (trunk_id, dispatch_rule_id) — store in the hospitals table.
    """
    if not hospital_slug or not did_number:
        return "", ""
    try:
        from livekit import api as lk_api
        lk = _lk()
        trunk = await lk.sip.create_sip_inbound_trunk(
            lk_api.CreateSIPInboundTrunkRequest(
                trunk=lk_api.SIPInboundTrunkInfo(
                    name=f"{hospital_slug} inbound (exotel)",
                    numbers=[did_number],
                    allowed_addresses=_EXOTEL_SIP_CIDRS,
                    krisp_enabled=True,
                )
            )
        )
        rule = await lk.sip.create_sip_dispatch_rule(
            lk_api.CreateSIPDispatchRuleRequest(
                trunk_ids=[trunk.sip_trunk_id],
                rule=lk_api.SIPDispatchRule(
                    dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                        room_prefix=f"{hospital_slug}-call-",
                    )
                ),
            )
        )
        await lk.aclose()
        logger.info(
            "sip_exotel_inbound_configured",
            slug=hospital_slug,
            did=did_number[-4:],
            trunk_id=trunk.sip_trunk_id,
            rule_id=rule.sip_dispatch_rule_id,
        )
        return trunk.sip_trunk_id, rule.sip_dispatch_rule_id
    except Exception as exc:
        logger.error("sip_exotel_inbound_setup_failed", slug=hospital_slug, error=str(exc))
        return "", ""


async def setup_sip_outbound_trunk() -> str:
    """
    Create (or return existing) Plivo SIP outbound trunk in LiveKit.
    Returns the trunk ID — set it as LIVEKIT_SIP_OUTBOUND_TRUNK_ID in env.
    """
    try:
        from livekit import api as lk_api
        lk = _lk()
        trunk = await lk.sip.create_sip_outbound_trunk(
            lk_api.CreateSIPOutboundTrunkRequest(
                trunk=lk_api.SIPOutboundTrunkInfo(
                    name="Plivo Outbound",
                    address="sip.plivo.com",
                    numbers=[settings.PLIVO_PHONE_NUMBER],
                    auth_username=settings.PLIVO_AUTH_ID,
                    auth_password=settings.PLIVO_AUTH_TOKEN,
                    transport=lk_api.SIPTransport.SIP_TRANSPORT_TLS,
                )
            )
        )
        await lk.aclose()
        logger.info("sip_outbound_trunk_created", trunk_id=trunk.sip_trunk_id)
        return trunk.sip_trunk_id
    except Exception as exc:
        logger.error("sip_outbound_trunk_failed", error=str(exc))
        return ""


async def setup_hospital_inbound(
    hospital_slug: str,
    did_number: str,
) -> tuple[str, str]:
    """
    Create a SIP inbound trunk + dispatch rule for one hospital DID.
    Every call to that DID spawns a new room named "{slug}-call-{uuid}".
    Returns (trunk_id, dispatch_rule_id) — store in the hospitals table.
    """
    if not hospital_slug or not did_number:
        return "", ""
    try:
        from livekit import api as lk_api
        lk = _lk()

        trunk = await lk.sip.create_sip_inbound_trunk(
            lk_api.CreateSIPInboundTrunkRequest(
                trunk=lk_api.SIPInboundTrunkInfo(
                    name=f"{hospital_slug} inbound",
                    numbers=[did_number],
                    allowed_addresses=_PLIVO_SIP_CIDRS,
                    krisp_enabled=True,
                )
            )
        )
        rule = await lk.sip.create_sip_dispatch_rule(
            lk_api.CreateSIPDispatchRuleRequest(
                trunk_ids=[trunk.sip_trunk_id],
                rule=lk_api.SIPDispatchRule(
                    dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                        room_prefix=f"{hospital_slug}-call-",
                    )
                ),
            )
        )
        await lk.aclose()
        logger.info(
            "sip_inbound_configured",
            slug=hospital_slug,
            did=did_number[-4:],
            trunk_id=trunk.sip_trunk_id,
            rule_id=rule.sip_dispatch_rule_id,
        )
        return trunk.sip_trunk_id, rule.sip_dispatch_rule_id
    except Exception as exc:
        logger.error("sip_inbound_setup_failed", slug=hospital_slug, error=str(exc))
        return "", ""


# ── Runtime: outbound calls ────────────────────────────────────────────────────

async def dial_outbound(
    patient_phone: str,
    hospital_slug: str,
    context: dict[str, Any],
    carrier: str = "auto",
) -> str:
    """
    Create a LiveKit room then dial the patient via SIP.

    carrier: "plivo" | "exotel" | "exotel_ws" | "auto" (auto picks Exotel if its
    trunk is set, falls back to Plivo). "exotel_ws" places the call over the
    Exotel Voicebot WebSocket instead of SIP — Exotel dials and streams audio
    to our bridge, which joins the LiveKit room we pre-create here.
    Returns the room name on success, "" on failure.
    """
    # Exotel WebSocket-streamed outbound: we pre-create the room (with context +
    # agent dispatch), then have Exotel dial the patient and stream to our WS.
    if carrier == "exotel_ws":
        room_name = await precreate_exotel_ws_room(hospital_slug, context)
        if not room_name:
            return ""
        from src.services.exotel_provisioning import connect_call_to_voicebot
        ok = await connect_call_to_voicebot(patient_phone, room_name)
        if not ok:
            await delete_room(room_name)
            return ""
        logger.info("outbound_exotel_ws_dialed", room=room_name, patient=patient_phone[-4:])
        return room_name

    exotel_trunk = settings.LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID
    plivo_trunk = settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID

    if carrier == "exotel":
        trunk_id = exotel_trunk
        sip_proxy = "sip.exotel.com"
    elif carrier == "plivo":
        trunk_id = plivo_trunk
        sip_proxy = "sip.plivo.com"
    else:  # auto
        if exotel_trunk:
            trunk_id = exotel_trunk
            sip_proxy = "sip.exotel.com"
        else:
            trunk_id = plivo_trunk
            sip_proxy = "sip.plivo.com"

    if not trunk_id:
        logger.error(
            "livekit_sip_outbound_not_configured",
            hint="Run POST /admin/sip/setup (Plivo) or /admin/sip/exotel/setup, then set the trunk ID env var",
        )
        return ""

    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY:
        logger.error("livekit_not_configured")
        return ""

    room_name = f"{hospital_slug}-call-{uuid.uuid4().hex[:8]}"

    lk = None
    try:
        from livekit import api as lk_api
        lk = _lk()

        # Room carries the outbound context so the agent can read it from metadata
        await lk.room.create_room(
            lk_api.CreateRoomRequest(
                name=room_name,
                metadata=json.dumps(context),
                empty_timeout=90,   # destroy if agent doesn't join within 90 s
                max_participants=3,
            )
        )

        phone = patient_phone if patient_phone.startswith("+") else f"+{patient_phone}"
        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone,
                room_name=room_name,
                participant_identity=f"patient-{phone[-4:]}",
                participant_name="Patient",
                play_ringtone=True,
                wait_until_answered=False,
            )
        )

        logger.info(
            "outbound_sip_dialed",
            patient=phone[-4:],
            room=room_name,
            carrier=sip_proxy.split(".")[1],
            call_type=context.get("call_type"),
        )
        return room_name

    except Exception as exc:
        logger.error("outbound_sip_failed", error=str(exc), patient=patient_phone[-4:])
        return ""
    finally:
        if lk is not None:
            await lk.aclose()


# ── Runtime: call transfer ────────────────────────────────────────────────────

async def transfer_call_in_room(
    room_name: str,
    to_phone: str,
    participant_name: str = "Department",
) -> bool:
    """
    Dial `to_phone` into an existing LiveKit room, completing a warm transfer.
    The patient and the department are bridged in the same room; the agent
    should then say goodbye and stop speaking.
    Returns True on success.
    """
    trunk_id = (
        settings.LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID
        or settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID
    )
    if not trunk_id:
        logger.warning(
            "sip_transfer_skipped",
            reason="No SIP outbound trunk configured — run /admin/sip/setup or /admin/sip/exotel/setup",
        )
        return False

    phone = to_phone.strip()
    if not phone:
        return False
    if not phone.startswith("+"):
        phone = f"+{phone}"

    lk = None
    try:
        from livekit import api as lk_api
        lk = _lk()
        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone,
                room_name=room_name,
                participant_identity=f"transfer-{phone[-4:]}",
                participant_name=participant_name,
                play_ringtone=True,
                wait_until_answered=False,
            )
        )
        logger.info("sip_transfer_dialed", room=room_name, dest=phone[-4:])
        return True
    except Exception as exc:
        logger.error("sip_transfer_failed", room=room_name, error=str(exc))
        return False
    finally:
        if lk is not None:
            await lk.aclose()


# ── Runtime: inbound ExoML (Exotel) ──────────────────────────────────────────

def get_inbound_exoml(to_number: str = "") -> str:
    """
    Returns Exotel ExoML that SIP-forwards an inbound call to LiveKit.
    ExoML is structurally identical to Plivo PCML for the Dial+Sip use case.
    The Exotel dispatch rule (matched by `to_number`) creates the agent room.
    """
    sip_host = settings.LIVEKIT_SIP_HOST
    if not sip_host:
        logger.warning(
            "livekit_sip_host_not_set",
            hint="Set LIVEKIT_SIP_HOST to your LiveKit SIP endpoint hostname",
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say>We are experiencing technical difficulties. "
            "Please call back in a few minutes. Thank you.</Say></Response>"
        )

    did = to_number or settings.EXOTEL_PHONE_NUMBER
    sip_uri = f"sip:{did}@{sip_host};transport=tls"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Dial>\n"
        f"    <Sip>{sip_uri}</Sip>\n"
        "  </Dial>\n"
        "</Response>"
    )


# ── Runtime: inbound Voicebot applet (Exotel WebSocket streaming) ─────────────

def _exotel_ws_url(slug: str) -> str:
    """WebSocket endpoint Exotel's Voicebot applet streams audio to."""
    token = settings.EXOTEL_WEBHOOK_TOKEN or "default"
    base = settings.PUBLIC_WS_URL.rstrip("/")
    return f"{base}/ws/exotel/stream/{token}/{slug}"


def get_voicebot_exoml(slug: str, room: str = "", extra_params: dict[str, str] | None = None) -> str:
    """
    Return Exotel Voicebot-applet ExoML that opens a bidirectional WebSocket.

    Exotel streams raw/slin 16-bit 8 kHz mono PCM (base64) to our WS, which
    bridges the audio into a LiveKit room where the agent answers. Custom
    `<Parameter>` children arrive on the WS `start` event as `custom_parameters`
    — we pass `room` for outbound so the bridge joins the pre-created room.
    """
    from xml.sax.saxutils import quoteattr

    url = _exotel_ws_url(slug)
    params = dict(extra_params or {})
    if room:
        params["room"] = room

    param_xml = "".join(
        f'      <Parameter name={quoteattr(k)} value={quoteattr(v)} />\n'
        for k, v in params.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Voicebot url={quoteattr(url)}>\n'
        f"{param_xml}"
        "    </Voicebot>\n"
        "  </Connect>\n"
        "</Response>"
    )


async def precreate_exotel_ws_room(hospital_slug: str, context: dict[str, Any]) -> str:
    """
    Create a LiveKit room (with outbound context + agent dispatch) for an
    Exotel WebSocket-streamed *outbound* call, returning the room name.

    The room is created up front so its name can be handed to Exotel as a
    custom parameter; the bridge then joins it when Exotel connects the WS.
    Returns "" on failure.
    """
    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY:
        logger.error("livekit_not_configured")
        return ""
    room_name = f"{hospital_slug}-call-{uuid.uuid4().hex[:8]}"
    lk = None
    try:
        from livekit import api as lk_api
        lk = _lk()
        await lk.room.create_room(
            lk_api.CreateRoomRequest(
                name=room_name,
                metadata=json.dumps(context),
                empty_timeout=90,
                max_participants=3,
                agents=[lk_api.RoomAgentDispatch(agent_name=settings.LIVEKIT_DISPATCH_NAME)],
            )
        )
        logger.info("exotel_ws_room_precreated", room=room_name, slug=hospital_slug)
        return room_name
    except Exception as exc:
        logger.error("exotel_ws_room_precreate_failed", slug=hospital_slug, error=str(exc))
        return ""
    finally:
        if lk is not None:
            await lk.aclose()


# ── Runtime: inbound PCML ─────────────────────────────────────────────────────

def get_inbound_pcml(to_number: str = "") -> str:
    """
    Returns Plivo PCML XML that SIP-forwards an inbound call to LiveKit.
    Plivo bridges the call: patient ↔ Plivo ↔ LiveKit SIP ↔ agent room.
    The LiveKit dispatch rule (matched by `to_number`) creates the room.
    """
    sip_host = settings.LIVEKIT_SIP_HOST
    if not sip_host:
        logger.warning(
            "livekit_sip_host_not_set",
            hint="Set LIVEKIT_SIP_HOST to your LiveKit SIP endpoint hostname",
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Speak>We are experiencing technical difficulties. "
            "Please call back in a few minutes. Thank you.</Speak></Response>"
        )

    # Use the DID that was called so LiveKit matches the right inbound trunk
    did = to_number or settings.PLIVO_PHONE_NUMBER
    sip_uri = f"sip:{did}@{sip_host};transport=tls"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Dial>\n"
        f"    <User>{sip_uri}</User>\n"
        "  </Dial>\n"
        "</Response>"
    )
