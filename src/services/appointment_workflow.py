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

import asyncio
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


# ── SMS/WhatsApp fire-and-forget helpers ──────────────────────────────────────

async def _send_reminder_sms(appt: dict, slot_local) -> None:
    """Send SMS/WhatsApp reminder as a backup to the outbound reminder call."""
    phone = appt.get("patient_phone", "")
    if not phone:
        return
    try:
        from src.services.whatsapp_service import get_messenger
        hospital_name = appt.get("hospital_name") or "the hospital"
        await get_messenger().send_appointment_reminder(
            phone=phone,
            hospital_name=hospital_name,
            patient_name=appt.get("patient_name") or "Patient",
            doctor_name=appt.get("doctor_name") or "",
            date=slot_local.strftime("%d %B %Y") if slot_local else "",
            time=slot_local.strftime("%I:%M %p") if slot_local else "",
        )
    except Exception as exc:
        logger.warning("reminder_sms_failed", error=str(exc))


async def _send_doctor_availability_sms(appt: dict, doctor_status: str) -> None:
    """Send SMS/WhatsApp doctor-availability update as backup to the outbound call."""
    phone = appt.get("patient_phone", "")
    if not phone:
        return
    try:
        from src.services.whatsapp_service import get_messenger
        hospital_name = appt.get("hospital_name") or "the hospital"
        slot = appt.get("slot_time")
        slot_local = slot.astimezone(INDIA_TZ) if slot and slot.tzinfo else (
            INDIA_TZ.localize(slot) if slot else None
        )
        await get_messenger().send_doctor_availability(
            phone=phone,
            hospital_name=hospital_name,
            patient_name=appt.get("patient_name") or "Patient",
            doctor_name=appt.get("doctor_name") or "",
            date=slot_local.strftime("%d %B %Y") if slot_local else "",
            status=doctor_status,
        )
    except Exception as exc:
        logger.warning("doctor_availability_sms_failed", error=str(exc))


async def _send_cancellation_sms(data: dict) -> None:
    """Send cancellation SMS/WhatsApp after workflow engine cancels an appointment.

    Takes a plain dict snapshotted while the DB connection was still held — the
    caller must NOT pass a live `conn`, because this runs as a detached task
    after the `async with pool.acquire()` block has returned the connection.
    """
    phone = data.get("patient_phone")
    if not phone:
        return
    try:
        from src.services.whatsapp_service import get_messenger
        slot = data.get("slot_time")
        slot_local = slot.astimezone(INDIA_TZ) if slot and slot.tzinfo else (
            INDIA_TZ.localize(slot) if slot else None
        )
        await get_messenger().send_appointment_cancellation(
            phone=phone,
            hospital_name=data.get("hospital_name") or "the hospital",
            patient_name=data.get("patient_name") or "Patient",
            doctor_name=data.get("doctor_name") or "",
            date=slot_local.strftime("%d %B %Y") if slot_local else "",
        )
    except Exception as exc:
        logger.warning("cancellation_sms_failed", error=str(exc))


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
        "appointment_id": appt_id,
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
        "appointment_id": appt_id,
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
            # Fire-and-forget SMS backup notification
            asyncio.ensure_future(_send_reminder_sms(appt, slot_local))
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

    slot = appt.get("slot_time")
    slot_local = slot.astimezone(INDIA_TZ) if slot and slot.tzinfo else (
        INDIA_TZ.localize(slot) if slot else None
    )
    context = {
        "call_type": "doctor_availability",
        "appointment_id": appt_id,
        "patient_name": appt.get("patient_name") or "",
        "doctor_name": appt.get("doctor_name") or "",
        "doctor_status": doctor_status,
        "appointment_date": slot_local.strftime("%d %B %Y") if slot_local else "",
        "appointment_time": slot_local.strftime("%I:%M %p") if slot_local else "",
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
            # SMS backup notification
            asyncio.ensure_future(_send_doctor_availability_sms(appt, doctor_status))
        await log_appointment_event(
            conn, appt_id, hospital_id,
            event_type="call_attempted" if room else "call_missed",
            note=f"doctor_availability attempt {attempts + 1}/{MAX_ATTEMPTS}, status={doctor_status}",
        )

    return bool(room)


# ── Outbound queue consumer ───────────────────────────────────────────────────

# Human-readable call-type labels for queue rows enqueued by the import flow.
_QUEUE_CALL_TYPES = {
    "reminder_24h": "24-hour reminder",
    "reminder_2h": "2-hour reminder",
    "doctor_unavailable": "doctor unavailable",
}


async def place_queue_call(
    pool,
    row: dict,
    tenant_slug: str = "default",
) -> bool:
    """Dial one ``outbound_call_queue`` row and persist the outcome.

    Used by the queue consumer for the trial tier's 24h / 2h reminder calls.
    Honours the calling window and the row's ``max_attempts``. On a successful
    dial the row is marked ``completed``; on failure the attempt count is bumped
    and the row reverts to ``pending`` (retried next pass) until ``max_attempts``,
    at which point it is marked ``max_attempts``.

    Returns True if a call was dialled (a room was created).
    """
    if not is_within_calling_hours():
        return False

    queue_id = str(row["id"])
    appt_id = str(row["appointment_id"]) if row.get("appointment_id") else None
    hospital_id = str(row.get("hospital_id") or "")
    call_type = row.get("call_type") or "reminder"
    phone = row.get("phone") or ""
    attempts = row.get("attempt_count", 0)
    max_attempts = row.get("max_attempts", MAX_ATTEMPTS)

    if not phone or attempts >= max_attempts:
        return False

    ctx = row.get("context_json") or {}
    if isinstance(ctx, str):
        try:
            import json as _json
            ctx = _json.loads(ctx)
        except Exception:
            ctx = {}
    context = {
        "call_type": call_type,
        "appointment_id": appt_id,
        "patient_name": row.get("patient_name") or ctx.get("patient_name") or "",
        "hospital_id": hospital_id,
        **{k: v for k, v in ctx.items() if k != "call_type"},
    }

    # Carry retry budget into the room so the agent can auto-redial an
    # answered-but-immediately-dropped call (early drop / dead air).
    context["_requeue"] = {
        "phone": phone,
        "call_type": call_type,
        "patient_name": context.get("patient_name") or "",
        "hospital_id": hospital_id,
        "appointment_id": appt_id,
        "tenant_slug": row.get("slug") or tenant_slug,
        "attempt": attempts + 1,
        "max_attempts": max_attempts,
    }

    room = await _dial_vobiz(phone, row.get("slug") or tenant_slug, context)

    next_attempt = attempts + 1
    new_status = (
        "completed" if room
        else ("max_attempts" if next_attempt >= max_attempts else "pending")
    )

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE outbound_call_queue
               SET attempt_count = $1, attempted_at = now(), status = $2,
                   livekit_room = COALESCE($3, livekit_room), updated_at = now()
               WHERE id = $4""",
            next_attempt, new_status, (room or None), queue_id,
        )
        if appt_id:
            label = _QUEUE_CALL_TYPES.get(call_type, call_type)
            await log_appointment_event(
                conn, appt_id, hospital_id,
                event_type="call_attempted" if room else "call_missed",
                note=f"{label} attempt {next_attempt}/{max_attempts}, room={room or 'none'}",
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
    # Snapshot the data we need for the SMS while the connection is still held,
    # then hand a plain dict to the detached task — the conn is released the
    # moment the caller's `async with pool.acquire()` block exits.
    row = await conn.fetchrow(
        """UPDATE appointments
           SET status = 'cancelled', workflow_status = 'cancelled', workflow_updated_at = NOW()
           WHERE id = $1
           RETURNING patient_phone, patient_name, doctor_name, slot_time""",
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
    # Fire-and-forget cancellation SMS using the snapshot (never the live conn).
    if row:
        asyncio.ensure_future(_send_cancellation_sms(dict(row)))
