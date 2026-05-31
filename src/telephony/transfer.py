"""
Real human call transfer for the Arteq hospital voice agent.

Today the bot only *speaks* the hospital number — it never actually connects
the caller to a person. This module performs a REAL transfer by bridging the
live caller to a human agent's phone using Exotel's Voice v1 "Connect two
numbers" API (``/v1/Accounts/<SID>/Calls/connect.json``).

How the bridge works
--------------------
Exotel's stream/voicebot leg cannot be "warm transferred" in place the way a
SIP REFER would work on a PBX. The supported, production approach is the
**outbound bridge call**: we ask Exotel to place a fresh call that first rings
the human agent (``From``) and, once the agent picks up, dials the caller
(``To``) and bridges the two legs together. The ExoPhone ``CallerId`` is used as
the outbound presentation number.

Limitation
----------
This starts a *new* bridged call rather than splicing the agent directly into
the existing media stream. The original voicebot WebSocket leg should be ended
by the caller's handler once this returns ``True`` (the human now owns the
conversation on the freshly bridged call). A ``200 OK`` from Exotel only means
the request was accepted — call outcome is delivered asynchronously via
``StatusCallback``; we therefore treat a 2xx response as "transfer initiated".

References
----------
- https://developer.exotel.com/api/make-a-call-api (Connect Two Numbers)
- https://developer.exotel.com/api/outgoing-call-to-connect-two-numbers
"""

from __future__ import annotations

import httpx

from src.config.settings import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)

# Connect API may take a while to set up both legs; keep a generous timeout.
_HTTP_TIMEOUT_SECONDS = 30.0


def _exotel_subdomain() -> str:
    """
    Resolve the Exotel API subdomain.

    Exotel exposes regional clusters (``api.exotel.com`` for the legacy/Mumbai
    cluster and ``api.in.exotel.com`` for the Singapore/India cluster). The
    correct one depends on where the account was provisioned, so it is made
    configurable via ``EXOTEL_SUBDOMAIN`` without requiring a settings change.
    """
    return getattr(settings, "EXOTEL_SUBDOMAIN", "api.exotel.com")


def _connect_url() -> str:
    sid = getattr(settings, "EXOTEL_SID", "")
    subdomain = _exotel_subdomain()
    return f"https://{subdomain}/v1/Accounts/{sid}/Calls/connect.json"


async def transfer_to_human(
    call_sid: str,
    agent_number: str,
    caller_number: str | None = None,
) -> bool:
    """
    Bridge the live call to a human agent's phone via Exotel.

    Places an Exotel "Connect two numbers" outbound call that rings the human
    ``agent_number`` first and, on answer, dials ``caller_number`` and bridges
    the two legs. Uses HTTP Basic auth (``EXOTEL_API_KEY``:``EXOTEL_API_TOKEN``)
    with the account SID in the URL path.

    Parameters
    ----------
    call_sid:
        Identifier of the in-progress call (used for logging/correlation; the
        bridge is a new call so this is not sent to Exotel as a leg).
    agent_number:
        The human agent / department phone number to ring first (``From``).
    caller_number:
        The original caller's number to connect to the agent (``To``). If not
        supplied the bridge cannot be placed and the function returns ``False``.

    Returns
    -------
    bool
        ``True`` if Exotel accepted the bridge request (2xx), else ``False``.
        A ``True`` result means the transfer was *initiated*; final connection
        outcome arrives asynchronously via Exotel's ``StatusCallback``.
    """
    api_key = getattr(settings, "EXOTEL_API_KEY", "")
    api_token = getattr(settings, "EXOTEL_API_TOKEN", "")
    sid = getattr(settings, "EXOTEL_SID", "")
    caller_id = getattr(settings, "EXOTEL_CALLER_ID", "")

    if not (api_key and api_token and sid and caller_id):
        logger.error(
            "transfer_misconfigured",
            call_sid=call_sid,
            has_key=bool(api_key),
            has_token=bool(api_token),
            has_sid=bool(sid),
            has_caller_id=bool(caller_id),
        )
        return False

    if not agent_number:
        logger.error("transfer_no_agent_number", call_sid=call_sid)
        return False

    if not caller_number:
        # Without the caller's number we have nothing to bridge the agent to.
        logger.error("transfer_no_caller_number", call_sid=call_sid)
        return False

    # Ring the human agent first; once they answer, connect the original caller.
    payload = {
        "From": agent_number,
        "To": caller_number,
        "CallerId": caller_id,
        "CallType": "trans",  # transactional call
        "TimeOut": "30",      # per-leg ring timeout (seconds)
    }

    url = _connect_url()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                data=payload,
                auth=(api_key, api_token),
            )
    except Exception as exc:  # network/timeout/etc.
        logger.error(
            "transfer_request_failed",
            call_sid=call_sid,
            agent_number=agent_number,
            error=str(exc),
        )
        return False

    if 200 <= resp.status_code < 300:
        logger.info(
            "transfer_initiated",
            call_sid=call_sid,
            agent_number=agent_number,
            caller_number=caller_number,
            status_code=resp.status_code,
        )
        return True

    logger.error(
        "transfer_rejected",
        call_sid=call_sid,
        agent_number=agent_number,
        status_code=resp.status_code,
        body=resp.text[:500],
    )
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Routing-destination → phone-number resolution
# ─────────────────────────────────────────────────────────────────────────────

# Synonyms / aliases the brain may emit for a given canonical destination.
_DESTINATION_ALIASES: dict[str, str] = {
    "reception": "reception",
    "front_desk": "reception",
    "frontdesk": "reception",
    "operator": "reception",
    "help_desk": "reception",
    "helpdesk": "reception",
    "emergency": "emergency",
    "casualty": "emergency",
    "ambulance": "emergency",
    "er": "emergency",
    "opd": "opd",
    "out_patient": "opd",
    "outpatient": "opd",
    "appointment": "opd",
    "appointments": "opd",
    "billing": "billing",
    "accounts": "billing",
    "cashier": "billing",
    "payment": "billing",
    "pharmacy": "pharmacy",
    "medicines": "pharmacy",
    "medical_store": "pharmacy",
    "lab": "lab",
    "laboratory": "lab",
    "diagnostics": "lab",
    "pathology": "lab",
    "patient_relations": "patient_relations",
    "patient_relation": "patient_relations",
    "grievance": "patient_relations",
    "complaints": "patient_relations",
    "doctor": "doctor",
    "consultation": "doctor",
    "physician": "doctor",
}


def resolve_transfer_target(
    destination: str,
    department_numbers: dict[str, str],
    hospital_phone: str,
    emergency_phone: str = "",
) -> str:
    """
    Map a routing ``destination`` to the best phone number to transfer to.

    Resolution order:
      1. Emergency destinations prefer ``emergency_phone`` (then a matching
         department number, then the hospital line).
      2. Other destinations use ``department_numbers`` keyed by the canonical
         destination (e.g. "billing", "pharmacy", "lab", "opd", "doctor",
         "patient_relations", "reception").
      3. Anything unresolved falls back to ``hospital_phone``.

    Parameters
    ----------
    destination:
        Routing label from the brain (case-insensitive; common aliases such as
        "casualty" → emergency, "front_desk" → reception are normalised).
    department_numbers:
        Mapping of canonical destination → direct phone number. Keys are matched
        case-insensitively.
    hospital_phone:
        Main hospital line used as the universal fallback.
    emergency_phone:
        Dedicated emergency/casualty number, used for emergency destinations.

    Returns
    -------
    str
        The resolved phone number. Falls back to ``hospital_phone`` (which may
        be an empty string if none is configured — callers should guard on that).
    """
    canonical = _DESTINATION_ALIASES.get((destination or "").strip().lower(), "")

    # Normalise the department map keys for case-insensitive lookups.
    dept_lookup = {
        (k or "").strip().lower(): (v or "").strip()
        for k, v in (department_numbers or {}).items()
    }

    def _dept(key: str) -> str:
        return dept_lookup.get(key, "")

    if canonical == "emergency":
        return emergency_phone.strip() or _dept("emergency") or hospital_phone

    if canonical:
        return _dept(canonical) or hospital_phone

    # Unknown destination — try a direct (non-aliased) match before falling back.
    direct = _dept((destination or "").strip().lower())
    return direct or hospital_phone
