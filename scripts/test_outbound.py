#!/usr/bin/env python3
"""
test_outbound.py — trigger a test outbound call to verify telephony + retry rule.

Run inside the app container (has DATABASE_URL + src on the path), from /app.

Modes:
  now    Dial the number immediately (ignores the 08:00–17:00 calling window).
         One call; Arya speaks when you answer. Quickest smoke test.
  queue  Enqueue a reminder in outbound_call_queue (max_attempts=3). The queue
         loop dials it within calling hours and RETRIES until you answer, up to
         3 attempts — this is what proves the "call until answered, 3×" rule.

Usage:
  python test_outbound.py --phone +9198XXXXXXXX --mode now   --name "Pranav"
  python test_outbound.py --phone +9198XXXXXXXX --mode queue --name "Pranav"
"""
import argparse, asyncio, asyncpg, os, json, uuid


def parse():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", required=True, help="Your number in +E.164, e.g. +9198XXXXXXXX")
    p.add_argument("--mode", choices=["now", "queue"], default="now")
    p.add_argument("--name", default="Test")
    p.add_argument("--slug", default="mother-hospital")
    return p.parse_args()


async def main():
    a = parse()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    hid = await conn.fetchval("SELECT id FROM hospitals WHERE slug=$1", a.slug)
    if not hid:
        await conn.close()
        raise SystemExit(f"hospital {a.slug} not found")

    ctx = {
        "call_type": "reminder",
        "patient_name": a.name,
        "doctor_name": "your doctor",
        "appointment_date": "today",
        "appointment_time": "soon",
        "hospital_id": str(hid),
    }

    if a.mode == "now":
        from src.services.vobiz_sip import dial_outbound_vobiz
        room = await dial_outbound_vobiz(a.phone, a.slug, ctx)
        print("dialed room:" , room or "(FAILED — check agent/SIP/trunk + that you answered)")
    else:
        await conn.execute(
            "INSERT INTO outbound_call_queue "
            "(hospital_id, call_type, phone, patient_name, context_json, scheduled_at, "
            " attempt_count, max_attempts, status, tenant_slug) "
            "VALUES ($1,'reminder',$2,$3,$4::jsonb, now(), 0, 3, 'pending', $5)",
            hid, a.phone, a.name, json.dumps(ctx), a.slug,
        )
        print("enqueued reminder for", a.phone)
        print("The queue loop dials within 08:00-17:00 IST, every ~5 min, retrying")
        print("up to 3 times until you answer. Watch: docker logs -f arteq-app-1")

    await conn.close()

asyncio.run(main())
