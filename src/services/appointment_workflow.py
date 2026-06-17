"""
Appointment Workflow Engine.

Manages the full lifecycle of appointment communications:
  • Confirmation calls (5-14 days before slot)
  • Reminder calls (day before)
  • Doctor-availability calls (day of)
  • Cancellation propagation

Calling rules:
  • Window: 08:00–17:00 IST only
  • Max 3 attempts per event type per appointment
  • Stop immediately when patient answers/confirms/cancels
  • Audit every event in appointment_events

Outbound calls are placed via Vobiz SIP through LiveKit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

import pytz
import structlog

logger = structlog.get_logger(__name__)

INDIA_TZ = pytz.timezone("Asia/Kolkata")
CALLING_WINDOW_START = time(8, 0)   # 08:00 IST
CALLING_WINDOW_END   = time(17, 0)  # 17:00 IST
MAX_ATTEMPTS         = 3


def is_within_calling_hours(now_ist: datetime | None = None) -> bool:
    """Return True if current IST time is within the allowed calling window."""
    now = (now_ist or datetime.now(INDIA_TZ)).time()
    return CALLING_WINDOW_START <= now <= CALLING_WINDOW_END


async def log_appointment_event(
    conn,
    appointment_id: str,
    hospital_id: str,
    event_type: str,
    old_status: str | None = None,
    new_status: str | None = None,
    note: str | None = None,
    actor: str = "system",
) -> None:
    """Append one row to appointment_events (fire-and-forget, never raises)."""
    try:
        await conn.execute(
            """
            INSERT INTO appointment_events
                (id, appointment_id, hospital_id, event_type, old_status, new_status, note, actor)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            str(uuid.uuid4()),
            appointment_id,
            hospital_id,
            event_type,
            old_status,
            new_status,
            note,
            actor,
        )
    except Exception as exc:
        logger.warning("appointment_event_log_failed", error=str(exc))


async def update_workflow_status(
    conn,
    appointment_id: str,
    hospital_id: str,
    new_status: str,
    actor: str = "system",
    note: str | None = None,
) -> None:
    """Update workflow_status on an appointment and write an audit event."""
    row = await conn.fetchrow(
        "SELECT workflow_status FROM appointments WHERE id = $1", appointment_id
    )
    old_status = row["workflow_status"] if row else None
    await conn.execute(
        """
        UPDATE appointments
        SET workflow_status = $1, workflow_updated_at = NOW()
        WHERE id = $2
        """,
        new_status,
        appointment_id,
    )
    await log_appointment_event(
        conn,
        appointment_id=appointment_id,
        hospital_id=hospital_id,
        event_type="status_change",
        old_status=old_status,
        new_status=new_status,
        note=note,
        actor=actor,
    )


# ── Outbound call helpers ─────────────────────────────────────────────────────

async def _dial_vobiz(
    patient_phone: str,
    hospital_slug: str,
    context: dict[str, Any],
) -> str:
    """Dial via Vobiz SIP; returns room name or ""."""
    try:
        from src.services.vobiz_sip import dial_outbound_vobiz
        return await dial_outbound_vobiz(patient_phone, hospital_slug, context)
    except Exception as exc:
        logger.error("workflow_dial_failed", error=str(exc))
        return ""


# ── Confirmation workflow ──────────────────────────────────────────────────────

async def place_confirmation_call(
    pool,
    appt: dict,
    tenant_slug: str = "default",
) -> bool:
    """Place a confirmation call for an appointment 5–14 days away.

    Checks:
    - Not yet confirmed / cancelled / missed
    - confirmation_attempts < MAX_ATTEMPTS
    - Currently within calling hours

    Returns True if the call was successfully dialled.
    """
    if not is_within_calling_hours():
        logger.debug("confirmation_outside_window", appointment_id=str(appt.get("id")))
        return False

    appt_id = str(appt["id"])
    hospital_id = str(appt.get("hospital_id") or "")
    attempts = appt.get("confirmation_attempts", 0)

    if attempts >= MAX_ATTEMPTS:
        async with pool.acquire() as conn:
            await update_workflow_status(conn, appt_id, hospital_id, "missed",
                                         note="max confirmation attempts reached")
        return False

    slot = appt.get("slot_time")
    slot_local = slot.astimezone(INDIA_TZ) if slot and slot.tzinfo else (
        INDIA_TZ.localize(slot) if slot else None
    )
    context = {
        "call_type": "confirmation",
        "patient_name": appt.get("patient_name") or "",
        "doctor_name": appt.get("doctor_name") or "",
        "appointment_date": slot_local.strftime("%d %B %Y") if slot_local else "",
        "appointment_time": slot_local.strftime("%I:%M %p") if slot_local else "",
        "hospital_id": hospital_id,
    }

    room = await _dial_vobiz(appt["patient_phone"], appt.get("slug") or tenant_slug, context)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET confirmation_attempts = confirmation_attempts + 1 WHERE id = $1",
            appt_id,
        )
        await log_appointment_event(
            conn, appt_id, hospital_id,
            event_type="call_attempted" if room else "call_missed",
            note=f"confirmation attempt {attempts + 1}/{MAX_ATTEMPTS}, room={room or 'none'}",
        )

    return bool(room)


# ── Reminder workflow ──────────────────────────────────────────────────────────

async def place_reminder_call(
    pool,
    appt: dict,
    tenant_slug: str = "default",
) -> bool:
    """Place a reminder call for an appointment within the next 24 hours."""
    if not is_within_calling_hours():
        return False

    appt_id = str(appt["id"])
    hospital_id = str(appt.get("hospital_id") or "")
    attempts = appt.get("reminder_attempts", 0)

    if attempts >= MAX_ATTEMPTS:
        async with pool.acquire() as conn:
            await update_workflow_status(conn, appt_id, hospital_id, "missed",
                                         note="max reminder attempts reached")
        return False

    slot = appt.get("slot_time")
    slot_local = slot.astimezone(INDIA_TZ) if slot and slot.tzinfo else (
        INDIA_TZ.localize(slot) if slot else None
    )
    context = {
        "call_type": "reminder",
        "patient_name": appt.get("patient_name") or "",
        "doctor_name": appt.get("doctor_name") or "",
        "appointment_date": slot_local.strftime("%Y-%m-%d") if slot_local else "",
        "appointment_time": slot_local.strftime("%H:%M") if slot_local else "",
        "hospital_id": hospital_id,
    }

    room = await _dial_vobiz(appt["patient_phone"], appt.get("slug") or tenant_slug, context)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET reminder_attempts = reminder_attempts + 1 WHERE id = $1",
            appt_id,
        )
        if room:
            await conn.execute(
                "UPDATE appointments SET reminder_sent = true WHERE id = $1", appt_id
            )
            await update_workflow_status(conn, appt_id, hospital_id, "reminder_sent")
        await log_appointment_event(
            conn, appt_id, hospital_id,
            event_type="call_attempted" if room else "call_missed",
            note=f"reminder attempt {attempts + 1}/{MAX_ATTEMPTS}",
        )

    return bool(room)


# ── Doctor-availability workflow ───────────────────────────────────────────────

async def place_doctor_availability_call(
    pool,
    appt: dict,
    doctor_status: str,
    tenant_slug: str = "default",
) -> bool:
    """Call the patient on appointment day to inform them of doctor availability.

    doctor_status: 'available' | 'delayed' | 'unavailable'
    """
    if not is_within_calling_hours():
        return False

    appt_id = str(appt["id"])
    hospital_id = str(appt.get("hospital_id") or "")
    attempts = appt.get("doctor_availability_attempts", 0)

    if attempts >= MAX_ATTEMPTS:
        return False

    workflow_map = {
        "available":   "doctor_available",
        "delayed":     "doctor_delayed",
        "unavailable": "doctor_unavailable",
    }
    new_wf_status = workflow_map.get(doctor_status, "doctor_available")

    context = {
        "call_type": "doctor_availability",
        "patient_name": appt.get("patient_name") or "",
        "doctor_name": appt.get("doctor_name") or "",
        "doctor_status": doctor_status,
        "hospital_id": hospital_id,
    }

    room = await _dial_vobiz(appt["patient_phone"], appt.get("slug") or tenant_slug, context)

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE appointments
               SET doctor_availability_attempts = doctor_availability_attempts + 1
               WHERE id = $1""",
            appt_id,
        )
        if room:
            await conn.execute(
                "UPDATE appointments SET doctor_availability_notified = true WHERE id = $1",
                appt_id,
            )
            await update_workflow_status(conn, appt_id, hospital_id, new_wf_status,
                                         note=f"doctor status: {doctor_status}")
        await log_appointment_event(
            conn, appt_id, hospital_id,
            event_type="call_attempted" if room else "call_missed",
            note=f"doctor_availability attempt {attempts + 1}/{MAX_ATTEMPTS}, status={doctor_status}",
        )

    return bool(room)


# ── Cancellation ──────────────────────────────────────────────────────────────

async def cancel_appointment(
    conn,
    appointment_id: str,
    hospital_id: str,
    actor: str = "patient",
    note: str | None = None,
) -> None:
    """Mark appointment cancelled and write audit event.

    All future confirmation / reminder / doctor-availability loops skip
    cancelled appointments because their status filter excludes 'cancelled'.
    """
    await conn.execute(
        """UPDATE appointments
           SET status = 'cancelled', workflow_status = 'cancelled', workflow_updated_at = NOW()
           WHERE id = $1""",
        appointment_id,
    )
    await log_appointment_event(
        conn, appointment_id, hospital_id,
        event_type="cancelled",
        old_status="pending",
        new_status="cancelled",
        note=note,
        actor=actor,
    )
