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
from src.db.queries import get_pool, set_tenant_db_url
from src.observability.logger import get_logger
from src.services.outbound_calls import OutboundCallService
from src.tenancy import registry

logger = get_logger(__name__)

# Signalled on shutdown so the loop can exit promptly between/within passes.
_stop = asyncio.Event()

_MARK_SENT = "UPDATE appointments SET reminder_sent = true WHERE id = $1"
_MARK_CONFIRMATION_SENT = "UPDATE appointments SET confirmation_sent = true WHERE id = $1"
_MARK_CALLBACK_SCHEDULED = "UPDATE callbacks SET status = 'scheduled' WHERE id = $1"
_MARK_FOLLOWUP_SENT = "UPDATE appointments SET followup_sent = true WHERE id = $1"


async def _scheduler_targets() -> list[tuple[str, str]]:
    """Every DB the scheduler must poll this pass.

    Always includes the control DB (empty db_url) for legacy/default-tenant
    data, plus each active tenant that has its own Supabase db_url. Tenants
    that share the control DB (no db_url) are covered by the control pass, so
    they are not duplicated.

    Returns a list of (slug, db_url) pairs. A failure to read the registry
    degrades gracefully to control-only so proactive calling never stalls.
    """
    targets: list[tuple[str, str]] = [("default", "")]
    seen = {""}
    try:
        tenants = await registry.list_tenants(include_inactive=False)
    except Exception as exc:
        logger.error("scheduler_tenant_list_failed", error=str(exc))
        return targets
    for t in tenants:
        url = (t.get("db_url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            targets.append((t.get("slug") or "tenant", url))
    return targets


async def _for_each_target(pass_name: str, body) -> None:
    """Run ``body(pool, slug)`` against the control DB and every active tenant DB.

    Binds each tenant's db_url into the query contextvar so all reads/writes in
    ``body`` route to the right Supabase instance. Per-target failures are caught
    and logged so one bad tenant DB never blocks the others.
    """
    for slug, db_url in await _scheduler_targets():
        if _stop.is_set():
            break
        set_tenant_db_url(db_url)
        try:
            pool = await get_pool()
            await body(pool, slug)
        except Exception as exc:
            logger.error(f"{pass_name}_target_failed", tenant=slug, error=str(exc))
        finally:
            set_tenant_db_url("")


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

    async def _pass(pool, tenant_slug):
        reminders = await service.get_pending_reminders(pool)
        if reminders:
            logger.info("reminder_pass", tenant=tenant_slug, pending=len(reminders))
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
                    tenant_slug=appt.get("slug") or tenant_slug,
                )
                if ok:
                    async with pool.acquire() as conn:
                        await conn.execute(_MARK_SENT, appt_id)
                    logger.info("reminder_sent", appointment_id=str(appt_id))
                else:
                    logger.warning("reminder_not_sent", appointment_id=str(appt_id))
            except Exception as exc:
                logger.error(
                    "reminder_appointment_failed",
                    appointment_id=str(appt_id),
                    error=str(exc),
                )

    while not _stop.is_set():
        await _for_each_target("reminder", _pass)

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
    from src.db.queries import get_pending_confirmations

    async def _pass(pool, tenant_slug):
        pending = await get_pending_confirmations(pool, days_min=days_min, days_max=days_max)
        if pending:
            logger.info("confirmation_pass", tenant=tenant_slug, pending=len(pending))
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
                    tenant_slug=appt.get("slug") or tenant_slug,
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

    while not _stop.is_set():
        await _for_each_target("confirmation", _pass)

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("confirmation_loop_stopped")


async def callback_loop(interval_seconds: int = 300) -> None:
    """Poll for pending callback requests and place outbound calls until stopped."""
    service = OutboundCallService()
    logger.info("callback_loop_started", interval_seconds=interval_seconds)

    async def _pass(pool, tenant_slug):
        pending = await service.get_pending_callbacks(pool)
        if pending:
            logger.info("callback_pass", tenant=tenant_slug, pending=len(pending))
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
                    tenant_slug=cb.get("slug") or tenant_slug,
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

    while not _stop.is_set():
        await _for_each_target("callback", _pass)

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("callback_loop_stopped")


async def followup_loop(interval_seconds: int = 3600, days_after: int = 3) -> None:
    """Hourly loop: call patients 3 days after their appointment to check on their well-being.

    Mirrors confirmation_loop: fetches pending follow-ups, places outbound calls via Exotel,
    then marks followup_sent = true on success. All failures are caught and logged.
    """
    service = OutboundCallService()
    logger.info("followup_loop_started", interval_seconds=interval_seconds, days_after=days_after)
    from src.db.queries import get_pending_followups

    async def _pass(pool, tenant_slug):
        pending = await get_pending_followups(pool, days_after=days_after)
        if pending:
            logger.info("followup_pass", tenant=tenant_slug, pending=len(pending))
        for appt in pending:
            if _stop.is_set():
                break
            appt_id = appt.get("id")
            try:
                ok = await service.schedule_followup_call(
                    patient_phone=appt["patient_phone"],
                    patient_name=appt.get("patient_name") or "",
                    doctor_name=appt.get("doctor_name") or "",
                    hospital_id=str(appt.get("hospital_id") or ""),
                    tenant_slug=appt.get("slug") or tenant_slug,
                )
                if ok:
                    async with pool.acquire() as conn:
                        await conn.execute(_MARK_FOLLOWUP_SENT, appt_id)
                    logger.info("followup_call_placed", appointment_id=str(appt_id))
                else:
                    logger.warning("followup_call_failed", appointment_id=str(appt_id))
            except Exception as exc:
                logger.error("followup_item_failed",
                             appointment_id=str(appt_id), error=str(exc))

    while not _stop.is_set():
        await _for_each_target("followup", _pass)

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("followup_loop_stopped")


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

    if getattr(settings, "FOLLOWUPS_ENABLED", True):
        fu_interval = getattr(settings, "FOLLOWUP_LOOP_INTERVAL_SECONDS", 3600)
        fu_days = getattr(settings, "FOLLOWUP_DAYS_AFTER", 3)
        asyncio.create_task(
            followup_loop(fu_interval, fu_days),
            name="followup_loop",
        )
        logger.info("followup_scheduler_started",
                    interval_seconds=fu_interval, days_after=fu_days)

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
