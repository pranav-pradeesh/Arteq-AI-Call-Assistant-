#!/usr/bin/env python3
"""
Outbound test call — dials a patient and has Arya confirm an appointment.

Use this to validate the OUTBOUND path end-to-end. It places one real call via
the configured carrier (TELEPHONY_CARRIER, default "vobiz") and the running agent
worker joins to speak. It does NOT set anything up — the prerequisites must
already be in place:

  • LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID set (run POST /admin/sip/vobiz/setup),
  • a Vobiz outbound trunk with a Credentials-List entry matching
    VOBIZ_SIP_USERNAME / VOBIZ_SIP_PASSWORD,
  • VOBIZ_PHONE_NUMBER allowed as the outbound caller-ID,
  • the agent worker (livekit_agent.py) running and registered with LiveKit.

Run inside the deployed environment (it needs the live LiveKit/Vobiz env):

    python tools/outbound_test_call.py \
        --phone +918848866921 --name Pranav \
        --doctor "Dr. <a real pediatrician>" --date 2026-06-23 --time 11:00 \
        --tenant kairali

Exit 0 = call was placed (your phone should ring and Arya confirms the slot).
Exit 1 = could not place the call (see the printed hint).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Place one outbound appointment-confirmation test call."
    )
    parser.add_argument("--phone", required=True,
                        help="Patient phone in E.164, e.g. +918848866921")
    parser.add_argument("--name", default="Pranav", help="Patient name")
    parser.add_argument("--doctor", required=True,
                        help="Doctor name — must exist in the hospital's doctors table")
    parser.add_argument("--date", required=True, help="Appointment date, YYYY-MM-DD")
    parser.add_argument("--time", required=True, help="Appointment time, HH:MM")
    parser.add_argument("--tenant", default="default",
                        help="Tenant slug (e.g. kairali) — must resolve in the registry")
    parser.add_argument("--hospital-id", default="", help="Hospital id (optional)")
    args = parser.parse_args()

    from src.services.outbound_calls import OutboundCallService

    masked = "*" * max(0, len(args.phone) - 4) + args.phone[-4:]
    print(f"[outbound-test] placing call to {masked} (tenant={args.tenant}) …",
          file=sys.stderr, flush=True)

    ok = await OutboundCallService().schedule_reminder(
        patient_phone=args.phone,
        patient_name=args.name,
        doctor_name=args.doctor,
        appointment_date=args.date,
        appointment_time=args.time,
        hospital_id=args.hospital_id,
        tenant_slug=args.tenant,
    )

    if ok:
        print("[outbound-test] ✓ call placed — your phone should ring and Arya will "
              "confirm the appointment.", file=sys.stderr, flush=True)
        return 0

    print("[outbound-test] ✗ could not place the call. Check:\n"
          "  • LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID points at a CURRENT trunk\n"
          "  • the Vobiz outbound trunk has a Credentials-List entry matching\n"
          "    VOBIZ_SIP_USERNAME / VOBIZ_SIP_PASSWORD\n"
          "  • VOBIZ_PHONE_NUMBER is allowed as outbound caller-ID\n"
          "  • the agent worker is running and registered with LiveKit",
          file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
