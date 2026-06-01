"""
Background scheduler — places proactive appointment-reminder calls.

A single long-lived asyncio task polls the appointments table on a fixed
interval. For every appointment due within the next 24 hours that has not
yet been reminded, it triggers an Exotel outbound reminder call and marks
``reminder_sent = true`` on success.

The loop is defensive: every per-appointment failure and every per-pass
failure is caught and logged so the task never dies. A module-level stop
event allows clean shutdown.
"""
from __future__ import annotations

import asyncio

from src.config.settings import settings
from src.db.queries import get_pool
from src.observability.logger import get_logger
from src.services.outbound_calls import OutboundCallService

logger = get_logger(__name__)

# Signalled on shutdown so the loop can exit promptly between/within passes.
_stop = asyncio.Event()

_MARK_SENT = "UPDATE appointments SET reminder_sent = true WHERE id = $1"
_MARK_CONFIRMATION_SENT = "UPDATE appointments SET confirmation_sent = true WHERE id = $1"
_MARK_CALLBACK_SCHEDULED = "UPDATE callbacks SET status = 'scheduled' WHERE id = $1"


async def reminder_loop(interval_seconds: int = 900) -> None:
    """Poll for due reminders and place outbound calls until stopped.

    Runs forever (until ``_stop`` is set). Each pass:
      1. acquires the shared DB pool,
      2. fetches pending reminders (next 24h, not yet reminded),
      3. for each, places an Exotel reminder call,
      4. marks ``reminder_sent = true`` on success.

    All exceptions are caught and logged; the loop never dies.
    """
    service = OutboundCallService()
    logger.info("reminder_loop_started", interval_seconds=interval_seconds)

    while not _stop.is_set():
        try:
            pool = await get_pool()
            reminders = await service.get_pending_reminders(pool)
            if reminders:
                logger.info("reminder_pass", pending=len(reminders))

            for appt in reminders:
                if _stop.is_set():
                    break
                appt_id = appt.get("id")
                try:
                    ok = await service.schedule_reminder(
                        patient_phone=appt["patient_phone"],
                        patient_name=appt.get("patient_name") or "",
                        doctor_name=appt.get("doctor_name") or "",
                        slot_time=appt["slot_time"],
                        hospital_id=str(appt.get("hospital_id") or ""),
                    )
                    if ok:
                        async with pool.acquire() as conn:
                            await conn.execute(_MARK_SENT, appt_id)
                        logger.info("reminder_sent", appointment_id=str(appt_id))
                    else:
                        logger.warning(
                            "reminder_not_sent", appointment_id=str(appt_id)
                        )
                except Exception as exc:
                    logger.error(
                        "reminder_appointment_failed",
                        appointment_id=str(appt_id),
                        error=str(exc),
                    )
        except Exception as exc:
            logger.error("reminder_pass_failed", error=str(exc))

        # Sleep between passes, but wake immediately on shutdown.
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("reminder_loop_stopped")


async def confirmation_loop(
    interval_seconds: int = 3600,
    days_min: int = 5,
    days_max: int = 14,
) -> None:
    """Daily loop: call patients whose appointments are 5–14 days away to confirm.

    Doctor books appointment → Arya calls patient 1–2 weeks before →
    patient says "confirm" or "reschedule" → Arya handles it conversationally.
    Runs every hour; the window check prevents duplicate calls.
    """
    service = OutboundCallService()
    logger.info("confirmation_loop_started",
                interval_seconds=interval_seconds, days_min=days_min, days_max=days_max)

    while not _stop.is_set():
        try:
            pool = await get_pool()
            from src.db.queries import get_pending_confirmations
            pending = await get_pending_confirmations(pool, days_min=days_min, days_max=days_max)
            if pending:
                logger.info("confirmation_pass", pending=len(pending))

            for appt in pending:
                if _stop.is_set():
                    break
                appt_id = appt.get("id")
                try:
                    ok = await service.schedule_confirmation_call(
                        patient_phone=appt["patient_phone"],
                        patient_name=appt.get("patient_name") or "",
                        doctor_name=appt.get("doctor_name") or "",
                        slot_time=appt.get("slot_time"),
                        hospital_id=str(appt.get("hospital_id") or ""),
                    )
                    if ok:
                        async with pool.acquire() as conn:
                            await conn.execute(_MARK_CONFIRMATION_SENT, appt_id)
                        logger.info("confirmation_call_placed", appointment_id=str(appt_id))
                    else:
                        logger.warning("confirmation_call_failed", appointment_id=str(appt_id))
                except Exception as exc:
                    logger.error("confirmation_item_failed",
                                 appointment_id=str(appt_id), error=str(exc))
        except Exception as exc:
            logger.error("confirmation_pass_failed", error=str(exc))

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("confirmation_loop_stopped")


async def callback_loop(interval_seconds: int = 300) -> None:
    """Poll for pending callback requests and place outbound calls until stopped."""
    service = OutboundCallService()
    logger.info("callback_loop_started", interval_seconds=interval_seconds)

    while not _stop.is_set():
        try:
            pool = await get_pool()
            pending = await service.get_pending_callbacks(pool)
            if pending:
                logger.info("callback_pass", pending=len(pending))

            for cb in pending:
                if _stop.is_set():
                    break
                cb_id = cb.get("id")
                try:
                    ok = await service.schedule_callback_call(
                        patient_phone=cb["patient_phone"],
                        patient_name=cb.get("patient_name") or "",
                        reason=cb.get("reason") or "",
                        hospital_id=str(cb.get("hospital_id") or ""),
                    )
                    if ok:
                        async with pool.acquire() as conn:
                            await conn.execute(_MARK_CALLBACK_SCHEDULED, cb_id)
                        logger.info("callback_scheduled", callback_id=str(cb_id))
                    else:
                        logger.warning("callback_not_scheduled", callback_id=str(cb_id))
                except Exception as exc:
                    logger.error("callback_item_failed",
                                 callback_id=str(cb_id), error=str(exc))
        except Exception as exc:
            logger.error("callback_pass_failed", error=str(exc))

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("callback_loop_stopped")


def start_scheduler() -> asyncio.Task | None:
    """Create and return the background reminder task.

    Returns ``None`` when reminders are disabled via ``REMINDERS_ENABLED``.
    """
    if not getattr(settings, "REMINDERS_ENABLED", True):
        logger.info("reminder_scheduler_disabled")
        return None

    _stop.clear()
    interval = getattr(settings, "REMINDER_INTERVAL_SECONDS", 900)
    task = asyncio.create_task(reminder_loop(interval), name="reminder_loop")
    logger.info("reminder_scheduler_started", interval_seconds=interval)

    if getattr(settings, "CALLBACKS_ENABLED", True):
        cb_interval = getattr(settings, "CALLBACK_LOOP_INTERVAL_SECONDS", 300)
        asyncio.create_task(callback_loop(cb_interval), name="callback_loop")
        logger.info("callback_scheduler_started", interval_seconds=cb_interval)

    if getattr(settings, "CONFIRMATIONS_ENABLED", True):
        conf_interval = getattr(settings, "CONFIRMATION_LOOP_INTERVAL_SECONDS", 3600)
        days_min = getattr(settings, "CONFIRMATION_DAYS_MIN", 5)
        days_max = getattr(settings, "CONFIRMATION_DAYS_MAX", 14)
        asyncio.create_task(
            confirmation_loop(conf_interval, days_min, days_max),
            name="confirmation_loop",
        )
        logger.info("confirmation_scheduler_started",
                    interval_seconds=conf_interval, days_min=days_min, days_max=days_max)

    return task


async def stop_scheduler(task: asyncio.Task | None) -> None:
    """Signal the loop to stop and await its cancellation cleanly."""
    _stop.set()
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    logger.info("reminder_scheduler_stopped")
