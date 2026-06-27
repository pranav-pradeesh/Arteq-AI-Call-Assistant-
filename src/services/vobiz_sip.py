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
                    # Vobiz routes each trunk on its OWN domain, <trunkId>.sip.vobiz.ai.
                    # Sending to the generic sip.vobiz.ai makes Vobiz reject with
                    # 404 "Trunk Not Found" — set VOBIZ_SIP_OUTBOUND_DOMAIN to the
                    # outbound trunk's SIP domain from the Vobiz console.
                    address=(
                        getattr(settings, "VOBIZ_SIP_OUTBOUND_DOMAIN", "")
                        or _VOBIZ_SIP_HOST
                    ),
                    numbers=[getattr(settings, "VOBIZ_PHONE_NUMBER", "")],
                    # Dedicated SIP credentials (match the Vobiz Credentials-List
                    # entry on the outbound trunk); fall back to the REST API
                    # key/secret for back-compat if the SIP creds aren't set.
                    auth_username=(
                        getattr(settings, "VOBIZ_SIP_USERNAME", "")
                        or getattr(settings, "VOBIZ_API_KEY", "")
                    ),
                    auth_password=(
                        getattr(settings, "VOBIZ_SIP_PASSWORD", "")
                        or getattr(settings, "VOBIZ_API_SECRET", "")
                    ),
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


async def _delete_existing_inbound(lk, lk_api, did_number: str) -> None:
    """Remove any inbound trunk that already claims ``did_number`` and the
    dispatch rules bound only to it, so inbound setup is idempotent and safe to
    re-run. Best-effort — logs and continues on any error."""
    try:
        trunks = await lk.sip.list_sip_inbound_trunk(
            lk_api.ListSIPInboundTrunkRequest()
        )
        stale = [t.sip_trunk_id for t in trunks.items if did_number in list(t.numbers)]
        if not stale:
            return
        rules = await lk.sip.list_sip_dispatch_rule(
            lk_api.ListSIPDispatchRuleRequest()
        )
        for r in rules.items:
            bound = list(r.trunk_ids)
            # Only delete rules scoped exclusively to the stale trunk(s); never a
            # catch-all rule (empty trunk_ids = applies to every trunk).
            if bound and all(tid in stale for tid in bound):
                try:
                    await lk.sip.delete_sip_dispatch_rule(
                        lk_api.DeleteSIPDispatchRuleRequest(
                            sip_dispatch_rule_id=r.sip_dispatch_rule_id
                        )
                    )
                except Exception as e:
                    logger.warning("vobiz_stale_rule_delete_failed", error=str(e))
        for tid in stale:
            try:
                await lk.sip.delete_sip_trunk(
                    lk_api.DeleteSIPTrunkRequest(sip_trunk_id=tid)
                )
            except Exception as e:
                logger.warning("vobiz_stale_trunk_delete_failed", trunk_id=tid, error=str(e))
        logger.info("vobiz_inbound_replaced", did=did_number[-4:], removed=len(stale))
    except Exception as exc:
        logger.warning("vobiz_inbound_cleanup_skipped", error=str(exc))


def _did_variants(did: str) -> list:
    """All formats a carrier might deliver the DID as, so an inbound call matches
    whether or not the caller dialed the country code: +91XXXXXXXXXX, 91XXXXXXXXXX,
    0XXXXXXXXXX, and the bare 10-digit number."""
    import re as _re
    digits = _re.sub(r"\D", "", did or "")
    digits = digits.lstrip("0")
    if not digits:
        return [did] if did else []
    if digits.startswith("91") and len(digits) >= 12:
        local = digits[2:]
    else:
        local = digits[-10:]
    out = []
    for v in (did, "+91" + local, "91" + local, local, "0" + local):
        if v and v not in out:
            out.append(v)
    return out


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

        # Idempotency: drop any existing inbound trunk that already claims this
        # DID (plus the dispatch rules bound to it) so re-running setup cleanly
        # replaces it instead of failing on "number already in use" or leaving a
        # stale, agent-less rule behind.
        await _delete_existing_inbound(lk, lk_api, did_number)

        trunk = await lk.sip.create_sip_inbound_trunk(
            lk_api.CreateSIPInboundTrunkRequest(
                trunk=lk_api.SIPInboundTrunkInfo(
                    name=f"{hospital_slug} inbound (vobiz)",
                    numbers=_did_variants(did_number),
                    allowed_addresses=_vobiz_cidrs(),
                    krisp_enabled=True,
                )
            )
        )
        # The dispatch rule MUST dispatch the agent into the room it creates. The
        # worker registers under an explicit agent_name (LIVEKIT_DISPATCH_NAME),
        # so without an agent dispatch here the inbound room is created but Arya
        # never joins — the caller hears dead air. (The outbound path already
        # dispatches the agent via RoomAgentDispatch on room creation.)
        rule = await lk.sip.create_sip_dispatch_rule(
            lk_api.CreateSIPDispatchRuleRequest(
                trunk_ids=[trunk.sip_trunk_id],
                rule=lk_api.SIPDispatchRule(
                    dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                        room_prefix=f"{hospital_slug}-call-",
                    )
                ),
                room_config=lk_api.RoomConfiguration(
                    agents=[lk_api.RoomAgentDispatch(
                        agent_name=settings.LIVEKIT_DISPATCH_NAME,
                    )],
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

def _format_vobiz_dest(phone_e164: str) -> str:
    """Format the callee number for Vobiz's outbound dial plan.

    Vobiz expects E.164 (+91…) per its docs, so that is the default. The earlier
    404s were a trunk-domain issue (generic sip.vobiz.ai vs <trunkId>.sip.vobiz.ai),
    not the number format. Override with VOBIZ_DIAL_FORMAT only if a given trunk
    needs a different shape:
      e164  → +91XXXXXXXXXX (default)   cc    → 91XXXXXXXXXX (digits, no +)
      national → 0XXXXXXXXXX            local → XXXXXXXXXX   (10-digit)
    """
    fmt = (getattr(settings, "VOBIZ_DIAL_FORMAT", "") or "e164").strip().lower()
    digits = phone_e164.lstrip("+")
    last10 = digits[-10:]
    if fmt == "national":
        return "0" + last10
    if fmt == "cc":
        return digits
    if fmt == "local":
        return last10
    return phone_e164 if phone_e164.startswith("+") else f"+{digits}"  # e164 (default)


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

    # Guard against empty/invalid numbers — without this a missing phone formats
    # to "0" and dials a bogus destination (seen in prod from a phone-less row).
    _digits = "".join(ch for ch in (patient_phone or "") if ch.isdigit())
    if len(_digits) < 10:
        logger.error("vobiz_outbound_bad_number", patient_phone=patient_phone or "")
        return ""

    room_name = f"{hospital_slug}-call-{uuid.uuid4().hex[:8]}"
    phone = patient_phone if patient_phone.startswith("+") else f"+{patient_phone}"
    dial_to = _format_vobiz_dest(phone)  # Vobiz dial-plan format (default E.164)

    # Carry the dialed number in the room metadata so the agent can attribute the
    # call_log to the right recipient. The SIP participant joins only after the
    # callee answers and its identity is just the last 4 digits ("patient-XXXX"),
    # so without this an outbound call_log records the caller as "unknown".
    context = {**(context or {}), "patient_phone": phone}

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
                # The number to dial, in Vobiz's dial-plan format (see
                # _format_vobiz_dest). The trunk supplies the SIP address
                # (sip.vobiz.ai) and the caller-ID (its `numbers`); this SDK has no
                # `sip_url` field — the callee goes in `sip_call_to`.
                sip_call_to=dial_to,
                room_name=room_name,
                participant_identity=f"patient-{phone[-4:]}",
                participant_name="Patient",
                play_ringtone=True,
                wait_until_answered=True,
            )
        )

        logger.info(
            "vobiz_outbound_dialed",
            patient=phone[-4:],
            dialed=dial_to,
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
