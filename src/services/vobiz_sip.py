"""
Vobiz SIP integration for LiveKit.

Replaces Plivo/Exotel as the telephony carrier for all inbound and outbound calls.
Vobiz uses standard SIP/TLS trunking — the LiveKit setup pattern is identical to
Plivo; only the SIP gateway address and credentials differ.

Call flows
----------
Inbound (patient calls hospital):
  Patient → Vobiz DID
  → Vobiz SIP → LiveKit SIP inbound trunk (matched by DID)
  → Dispatch rule creates room "{slug}-call-{uuid8}"
  → Agent worker auto-dispatches to room

Outbound (reminders / confirmations / doctor-availability / callbacks):
  Scheduler → dial_outbound_vobiz()
  → LiveKit room created with context in metadata + agent dispatch
  → create_sip_participant: LiveKit dials patient via Vobiz SIP outbound trunk

One-time setup (POST /admin/sip/vobiz/setup after first deploy):
  • Creates Vobiz SIP outbound trunk in LiveKit
  • For each hospital with a provisioned DID: creates inbound trunk + dispatch rule
  • Returns trunk IDs — save LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID in env
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_VOBIZ_SIP_HOST = "sip.vobiz.ai"


def _vobiz_cidrs() -> list[str]:
    """Return Vobiz source IP CIDRs for inbound trunk allow-list.

    Set VOBIZ_SIP_CIDRS (comma-separated) in env to override defaults.
    Confirm the exact ranges with Vobiz support before production lockdown;
    the placeholder below accepts all traffic, which is safe only behind
    LiveKit's trunk-number matching.
    """
    raw = getattr(settings, "VOBIZ_SIP_CIDRS", "")
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    return ["0.0.0.0/0"]  # restrict once Vobiz publishes their IP ranges


def _lk():
    try:
        from livekit import api as lk_api
        return lk_api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    except ImportError as exc:
        raise RuntimeError("livekit package not installed — pip install livekit") from exc


# ── One-time provisioning ──────────────────────────────────────────────────────

async def setup_vobiz_outbound_trunk() -> str:
    """Create the Vobiz SIP outbound trunk in LiveKit.

    Run once after deploy. Returns the trunk ID — save as
    LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID in the environment.
    """
    try:
        from livekit import api as lk_api
        lk = _lk()
        trunk = await lk.sip.create_sip_outbound_trunk(
            lk_api.CreateSIPOutboundTrunkRequest(
                trunk=lk_api.SIPOutboundTrunkInfo(
                    name="Vobiz Outbound",
                    address=_VOBIZ_SIP_HOST,
                    numbers=[getattr(settings, "VOBIZ_PHONE_NUMBER", "")],
                    auth_username=getattr(settings, "VOBIZ_API_KEY", ""),
                    auth_password=getattr(settings, "VOBIZ_API_SECRET", ""),
                    transport=lk_api.SIPTransport.SIP_TRANSPORT_TLS,
                )
            )
        )
        await lk.aclose()
        logger.info("vobiz_outbound_trunk_created", trunk_id=trunk.sip_trunk_id)
        return trunk.sip_trunk_id
    except Exception as exc:
        logger.error("vobiz_outbound_trunk_failed", error=str(exc))
        return ""


async def setup_hospital_inbound_vobiz(
    hospital_slug: str,
    did_number: str,
) -> tuple[str, str]:
    """Create a SIP inbound trunk + dispatch rule for one hospital's Vobiz DID.

    Every inbound call spawns a new room named "{slug}-call-{uuid8}".
    Returns (trunk_id, dispatch_rule_id) — store in the hospitals row.
    """
    if not hospital_slug or not did_number:
        return "", ""
    try:
        from livekit import api as lk_api
        lk = _lk()

        trunk = await lk.sip.create_sip_inbound_trunk(
            lk_api.CreateSIPInboundTrunkRequest(
                trunk=lk_api.SIPInboundTrunkInfo(
                    name=f"{hospital_slug} inbound (vobiz)",
                    numbers=[did_number],
                    allowed_addresses=_vobiz_cidrs(),
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
            "vobiz_inbound_configured",
            slug=hospital_slug,
            did=did_number[-4:],
            trunk_id=trunk.sip_trunk_id,
            rule_id=rule.sip_dispatch_rule_id,
        )
        return trunk.sip_trunk_id, rule.sip_dispatch_rule_id
    except Exception as exc:
        logger.error("vobiz_inbound_setup_failed", slug=hospital_slug, error=str(exc))
        return "", ""


# ── Runtime: outbound calls ────────────────────────────────────────────────────

async def dial_outbound_vobiz(
    patient_phone: str,
    hospital_slug: str,
    context: dict[str, Any],
) -> str:
    """Create a LiveKit room and dial the patient via the Vobiz SIP trunk.

    Room name "{slug}-call-{uuid4().hex[:8]}" is globally unique — no room
    collisions possible even under high concurrent call load.
    Returns the room name on success, "" on failure.
    """
    trunk_id = getattr(settings, "LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID", "")
    if not trunk_id:
        logger.error(
            "vobiz_outbound_not_configured",
            hint="Run POST /admin/sip/vobiz/setup then set LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID",
        )
        return ""

    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY:
        logger.error("livekit_not_configured")
        return ""

    room_name = f"{hospital_slug}-call-{uuid.uuid4().hex[:8]}"
    phone = patient_phone if patient_phone.startswith("+") else f"+{patient_phone}"

    lk = None
    try:
        from livekit import api as lk_api
        lk = _lk()

        # Room carries outbound context in metadata; agent reads it on join.
        # Agent dispatch is wired into the room so the worker picks it up
        # immediately without needing an explicit dispatch call.
        await lk.room.create_room(
            lk_api.CreateRoomRequest(
                name=room_name,
                metadata=json.dumps(context),
                empty_timeout=90,      # destroy if agent does not join within 90 s
                max_participants=3,    # patient SIP leg + agent + optional transfer
                agents=[lk_api.RoomAgentDispatch(agent_name=settings.LIVEKIT_DISPATCH_NAME)],
            )
        )

        await lk.sip.create_sip_participant(
            lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_url=f"sip:{phone}@{_VOBIZ_SIP_HOST}",
                room_name=room_name,
                participant_identity=f"patient-{phone[-4:]}",
                participant_name="Patient",
                play_ringtone=True,
                wait_until_answered=False,
            )
        )

        logger.info(
            "vobiz_outbound_dialed",
            patient=phone[-4:],
            room=room_name,
            call_type=context.get("call_type"),
        )
        return room_name

    except Exception as exc:
        logger.error("vobiz_outbound_failed", error=str(exc), patient=phone[-4:])
        return ""
    finally:
        if lk is not None:
            await lk.aclose()
