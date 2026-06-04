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
) -> str:
    """
    Create a LiveKit room (with outbound context as metadata), then instruct
    LiveKit to dial the patient via the Plivo SIP outbound trunk.
    The agent worker auto-dispatches to this room when the patient answers.
    Returns the room name on success, "" on failure.
    """
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        logger.error(
            "livekit_sip_outbound_not_configured",
            hint="Run POST /admin/sip/setup then set LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
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

        # LiveKit dials the patient via Plivo SIP
        # Plivo expects E.164 format (+91XXXXXXXXXX)
        phone = patient_phone if patient_phone.startswith("+") else f"+{patient_phone}"
        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID,
                sip_url=f"sip:{phone}@sip.plivo.com",
                room_name=room_name,
                participant_identity=f"patient-{phone[-4:]}",
                participant_name="Patient",
                play_ringtone=True,
                wait_until_answered=False,  # non-blocking — agent joins while ringing
            )
        )

        logger.info(
            "outbound_sip_dialed",
            patient=phone[-4:],
            room=room_name,
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
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        logger.warning(
            "sip_transfer_skipped",
            reason="LIVEKIT_SIP_OUTBOUND_TRUNK_ID not set — update env after running SIP setup",
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
                sip_trunk_id=settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID,
                sip_url=f"sip:{phone}@sip.plivo.com",
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
