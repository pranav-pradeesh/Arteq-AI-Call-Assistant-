"""
Background scheduler — proactive appointment-workflow calls via Vobiz SIP.

Loops
-----
reminder_loop        Every 15 min — reminder calls for appointments in the next 24 h
confirmation_loop    Every 60 min — confirmation calls 5–14 days before slot
doctor_avail_loop    Every 10 min — day-of doctor-availability calls
callback_loop        Every 5 min  — pending callback requests
followup_loop        Every 60 min — post-visit follow-up calls 3 days after slot
campaign_resume_loop Every 10 min — resumes campaigns stalled by server restart

Calling rules (enforced here and in appointment_workflow.py):
  • Never call before 08:00 IST or after 17:00 IST
  • Max 3 attempts per event type per appointment
  • Stop all future calls once patient confirms / cancels
"""
from __future__ import annotations

import asyncio

from src.config.settings import settings
from src.db.queries import get_pool, set_tenant_db_url
from src.observability.logger import get_logger
from src.services.appointment_workflow import (
    MAX_ATTEMPTS,
    is_within_calling_hours,
    place_confirmation_call,
    place_doctor_availability_call,
    place_queue_call,
    place_reminder_call,
)
from src.services.outbound_calls import OutboundCallService
from src.tenancy import registry

logger = get_logger(__name__)

_stop = asyncio.Event()

_MARK_CALLBACK_SCHEDULED = "UPDATE callbacks SET status = 'scheduled' WHERE id = $1"
_MARK_FOLLOWUP_SENT = "UPDATE appointments SET followup_sent = true WHERE id = $1"


async def _scheduler_targets() -> list[tuple[str, str]]:
    """Return (slug, db_url) for the control DB and every active tenant.

    A failure to read the registry degrades gracefully to control-DB-only.
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
    """Run body(pool, slug) against every active DB.

    Per-target failures are caught so one bad tenant never blocks others.
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


# ── Reminder loop ─────────────────────────────────────────────────────────────

async def reminder_loop(interval_seconds: int = 900) -> None:
    """Place reminder calls for appointments due in the next 24 hours."""
    logger.info("reminder_loop_started", interval_seconds=interval_seconds)

    _PENDING = """
        SELECT a.id, a.patient_phone, a.patient_name, a.slot_time,
               a.hospital_id, a.reminder_attempts, a.last_reminder_at,
               d.name AS doctor_name, h.slug AS slug, h.name AS hospital_name
        FROM appointments a
        LEFT JOIN doctors  d ON a.doctor_id   = d.id
        LEFT JOIN hospitals h ON h.id = a.hospital_id
        WHERE a.reminder_sent = false
          AND a.reminder_attempts < $1
          AND a.status IN ('booked', 'confirmed')
          AND a.workflow_status NOT IN ('cancelled', 'missed')
          AND a.slot_time > now()
          AND (a.slot_time AT TIME ZONE 'Asia/Kolkata')::date
              <= ((now() AT TIME ZONE 'Asia/Kolkata')::date + 1)
        ORDER BY a.slot_time
    """
    # Imported (trial) appointments get their 24h + 2h reminders from the
    # queue consumer (outbound_queue_loop), so they are excluded here to avoid
    # a duplicate single-shot reminder call.

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PENDING, MAX_ATTEMPTS)
        if rows:
            logger.info("reminder_pass", tenant=tenant_slug, pending=len(rows))
        for appt in rows:
            if _stop.is_set():
                break
            try:
                await place_reminder_call(pool, dict(appt), tenant_slug)
            except Exception as exc:
                logger.error("reminder_item_failed",
                             appointment_id=str(appt["id"]), error=str(exc))

    while not _stop.is_set():
        await _for_each_target("reminder", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("reminder_loop_stopped")


# ── Confirmation loop ─────────────────────────────────────────────────────────

async def confirmation_loop(
    interval_seconds: int = 3600,
    days_min: int = 5,
    days_max: int = 14,
) -> None:
    """Place confirmation calls for appointments 5–14 days away."""
    logger.info("confirmation_loop_started",
                interval_seconds=interval_seconds, days_min=days_min, days_max=days_max)

    _PENDING = """
        SELECT a.id, a.patient_phone, a.patient_name, a.slot_time,
               a.hospital_id, a.confirmation_attempts,
               d.name AS doctor_name, h.slug AS slug
        FROM appointments a
        LEFT JOIN doctors  d ON a.doctor_id   = d.id
        LEFT JOIN hospitals h ON h.id = a.hospital_id
        WHERE a.confirmation_sent = false
          AND a.confirmation_attempts < $1
          AND a.status IN ('booked', 'confirmed', 'pending')
          AND a.workflow_status NOT IN ('confirmed', 'cancelled', 'missed')
          AND a.slot_time BETWEEN now() + ($2 || ' days')::interval
                              AND now() + ($3 || ' days')::interval
        ORDER BY a.slot_time
    """

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PENDING, MAX_ATTEMPTS, str(days_min), str(days_max))
        if rows:
            logger.info("confirmation_pass", tenant=tenant_slug, pending=len(rows))
        for appt in rows:
            if _stop.is_set():
                break
            try:
                ok = await place_confirmation_call(pool, dict(appt), tenant_slug)
                if ok:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE appointments SET confirmation_sent = true WHERE id = $1",
                            appt["id"],
                        )
            except Exception as exc:
                logger.error("confirmation_item_failed",
                             appointment_id=str(appt["id"]), error=str(exc))

    while not _stop.is_set():
        await _for_each_target("confirmation", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("confirmation_loop_stopped")


# ── Doctor-availability loop ───────────────────────────────────────────────────

async def doctor_availability_loop(interval_seconds: int = 600) -> None:
    """On appointment day, call patients to inform them of doctor availability.

    Only fires for doctors whose availability_status != 'available', or for all
    appointment-day patients to confirm doctor IS available. Fetches appointments
    for today where doctor_availability_notified = false.
    """
    logger.info("doctor_availability_loop_started", interval_seconds=interval_seconds)

    _PENDING = """
        SELECT a.id, a.patient_phone, a.patient_name, a.slot_time,
               a.hospital_id, a.doctor_availability_attempts,
               a.doctor_availability_notified,
               d.name AS doctor_name,
               d.availability_status AS doctor_status,
               h.slug AS slug, h.name AS hospital_name
        FROM appointments a
        LEFT JOIN doctors   d ON a.doctor_id = d.id
        LEFT JOIN hospitals h ON h.id        = a.hospital_id
        WHERE a.doctor_availability_notified = false
          AND a.doctor_availability_attempts < $1
          AND a.status IN ('booked', 'confirmed')
          AND a.workflow_status NOT IN ('cancelled', 'missed', 'doctor_available',
                                        'doctor_delayed', 'doctor_unavailable')
          AND COALESCE(h.call_on_doctor_unavailable_enabled, true) = true
          AND DATE(a.slot_time AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
        ORDER BY a.slot_time
    """

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PENDING, MAX_ATTEMPTS)
        if rows:
            logger.info("doctor_avail_pass", tenant=tenant_slug, pending=len(rows))
        for appt in rows:
            if _stop.is_set():
                break
            doctor_status = appt.get("doctor_status") or "available"
            try:
                await place_doctor_availability_call(
                    pool, dict(appt), doctor_status, tenant_slug
                )
            except Exception as exc:
                logger.error("doctor_avail_item_failed",
                             appointment_id=str(appt["id"]), error=str(exc))

    while not _stop.is_set():
        await _for_each_target("doctor_availability", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("doctor_availability_loop_stopped")


# ── Outbound queue loop ────────────────────────────────────────────────────────

async def outbound_queue_loop(interval_seconds: int = 300) -> None:
    """Drain due rows from outbound_call_queue (trial 24h/2h reminder calls).

    The import endpoint enqueues a reminder_24h row at slot-24h and a reminder_2h
    row at slot-2h (subject to the hospital's toggles). This loop dials any row
    whose scheduled_at has arrived, within the calling window, with retry up to
    max_attempts. Other producers (campaigns, etc.) may share this table, so the
    loop is call_type-agnostic and simply honours scheduled_at / status.
    """
    logger.info("outbound_queue_loop_started", interval_seconds=interval_seconds)

    _DUE = """
        SELECT q.id, q.appointment_id, q.hospital_id, q.call_type, q.phone,
               q.patient_name, q.context_json, q.scheduled_at,
               q.attempt_count, q.max_attempts, q.tenant_slug,
               h.slug AS slug
        FROM outbound_call_queue q
        LEFT JOIN hospitals h ON h.id = q.hospital_id
        WHERE q.status = 'pending'
          AND q.attempt_count < q.max_attempts
          AND q.scheduled_at <= now()
        ORDER BY q.scheduled_at
        LIMIT 50
    """

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            rows = await conn.fetch(_DUE)
        if rows:
            logger.info("outbound_queue_pass", tenant=tenant_slug, due=len(rows))
        for row in rows:
            if _stop.is_set():
                break
            try:
                await place_queue_call(pool, dict(row), row["slug"] or tenant_slug)
            except Exception as exc:
                logger.error("outbound_queue_item_failed",
                             queue_id=str(row["id"]), error=str(exc))
            await asyncio.sleep(1.0)  # pace the SIP trunk

    while not _stop.is_set():
        await _for_each_target("outbound_queue", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("outbound_queue_loop_stopped")


# ── Vobiz CDR cost reconciliation loop ─────────────────────────────────────────

async def cdr_reconcile_loop(interval_seconds: int = 600) -> None:
    """Pull each outbound call's REAL billed INR cost from the Vobiz CDR API and
    write it onto call_logs (replacing the duration-based telephony estimate)."""
    logger.info("cdr_reconcile_loop_started", interval_seconds=interval_seconds)

    async def _pass(pool, tenant_slug):
        try:
            from src.services.vobiz_billing import reconcile_cdr_costs
            n = await reconcile_cdr_costs(pool)
            if n:
                logger.info("cdr_reconcile_pass", tenant=tenant_slug, reconciled=n)
        except Exception as exc:
            logger.error("cdr_reconcile_failed", tenant=tenant_slug, error=str(exc))

    while not _stop.is_set():
        await _for_each_target("cdr_reconcile", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("cdr_reconcile_loop_stopped")


# ── Callback loop ─────────────────────────────────────────────────────────────

async def callback_loop(interval_seconds: int = 300) -> None:
    """Place outbound calls for pending patient callback requests."""
    service = OutboundCallService()
    logger.info("callback_loop_started", interval_seconds=interval_seconds)

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            if settings.AFTER_HOURS_CALLBACK_ENABLED:
                pending = await service.get_pending_callbacks(pool)
                if pending:
                    logger.info(
                        "callback_after_hours_queued",
                        tenant=tenant_slug,
                        queued=len(pending),
                        note="will_process_at_08:00_IST",
                    )
            return
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
            except Exception as exc:
                logger.error("callback_item_failed", callback_id=str(cb_id), error=str(exc))

    while not _stop.is_set():
        await _for_each_target("callback", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("callback_loop_stopped")


# ── Followup loop ─────────────────────────────────────────────────────────────

async def followup_loop(interval_seconds: int = 3600, days_after: int = 3) -> None:
    """Post-visit follow-up calls placed 3 days after the appointment slot."""
    service = OutboundCallService()
    logger.info("followup_loop_started", interval_seconds=interval_seconds, days_after=days_after)

    _PENDING = """
        SELECT a.id, a.patient_phone, a.patient_name, a.slot_time,
               a.hospital_id, a.followup_attempts,
               d.name AS doctor_name, h.slug AS slug
        FROM appointments a
        LEFT JOIN doctors  d ON a.doctor_id = d.id
        LEFT JOIN hospitals h ON h.id       = a.hospital_id
        WHERE a.followup_sent = false
          AND a.followup_attempts < $1
          AND a.status IN ('confirmed', 'booked')
          AND a.slot_time < now() - ($2 || ' days')::interval
        ORDER BY a.slot_time DESC
        LIMIT 20
    """

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PENDING, MAX_ATTEMPTS, str(days_after))
        if rows:
            logger.info("followup_pass", tenant=tenant_slug, pending=len(rows))
        for appt in rows:
            if _stop.is_set():
                break
            appt_id = appt.get("id")
            try:
                from src.db.queries import increment_outbound_attempts
                await increment_outbound_attempts(pool, appt_id, "followup")
                ok = await service.schedule_followup_call(
                    patient_phone=appt["patient_phone"],
                    patient_name=appt.get("patient_name") or "",
                    doctor_name=appt.get("doctor_name") or "",
                    hospital_id=str(appt.get("hospital_id") or ""),
                    tenant_slug=appt.get("slug") or tenant_slug,
                )
                if ok:
                    async with pool.acquire() as conn2:
                        await conn2.execute(_MARK_FOLLOWUP_SENT, appt_id)
                    logger.info("followup_call_placed", appointment_id=str(appt_id))
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


# ── Campaign resume loop ──────────────────────────────────────────────────────

async def campaign_resume_loop(interval_seconds: int = 600) -> None:
    """Resume stalled bulk-outbound campaigns after a server restart."""
    service = OutboundCallService()
    logger.info("campaign_resume_loop_started", interval_seconds=interval_seconds)

    _STALLED = """
        SELECT c.id, c.campaign_type, c.message_template, c.hospital_id, h.slug
        FROM campaigns c
        LEFT JOIN hospitals h ON h.id = c.hospital_id
        WHERE c.status = 'running'
          AND c.updated_at < now() - interval '15 minutes'
          AND EXISTS (SELECT 1 FROM campaign_recipients r
                      WHERE r.campaign_id = c.id AND r.call_status = 'pending')
        LIMIT 5
    """
    _PENDING = """
        SELECT phone FROM campaign_recipients
        WHERE campaign_id = $1 AND call_status = 'pending'
        ORDER BY id LIMIT 50
    """

    async def _pass(pool, tenant_slug):
        if not is_within_calling_hours():
            return
        async with pool.acquire() as conn:
            stalled = await conn.fetch(_STALLED)
        for c in stalled:
            campaign_id = str(c["id"])
            logger.info("campaign_resumed", campaign_id=campaign_id, tenant=tenant_slug)
            async with pool.acquire() as conn:
                phones = [r["phone"] for r in await conn.fetch(_PENDING, c["id"])]
            for phone in phones:
                if _stop.is_set():
                    return
                try:
                    ok = await service.schedule_campaign_call(
                        patient_phone=phone,
                        patient_name="",
                        campaign_type=c["campaign_type"] or "custom",
                        campaign_message=c["message_template"] or "",
                        hospital_id=str(c["hospital_id"] or ""),
                        campaign_id=campaign_id,
                        tenant_slug=c["slug"] or tenant_slug,
                    )
                    async with pool.acquire() as conn:
                        if ok:
                            await conn.execute(
                                "UPDATE campaign_recipients SET call_status='called',"
                                " called_at=now() WHERE campaign_id=$1 AND phone=$2",
                                c["id"], phone,
                            )
                            await conn.execute(
                                "UPDATE campaigns SET calls_placed = calls_placed + 1,"
                                " updated_at = now() WHERE id=$1", c["id"],
                            )
                        else:
                            await conn.execute(
                                "UPDATE campaign_recipients SET call_status='failed'"
                                " WHERE campaign_id=$1 AND phone=$2",
                                c["id"], phone,
                            )
                except Exception as exc:
                    logger.error("campaign_resume_item_failed",
                                 campaign_id=campaign_id, error=str(exc))
                await asyncio.sleep(2.0)
            async with pool.acquire() as conn:
                remaining = await conn.fetchval(
                    "SELECT 1 FROM campaign_recipients "
                    "WHERE campaign_id=$1 AND call_status='pending' LIMIT 1", c["id"],
                )
                if not remaining:
                    await conn.execute(
                        "UPDATE campaigns SET status='completed', updated_at=now() "
                        "WHERE id=$1", c["id"],
                    )
                    logger.info("campaign_completed_on_resume", campaign_id=campaign_id)

    while not _stop.is_set():
        await _for_each_target("campaign_resume", _pass)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

    logger.info("campaign_resume_loop_stopped")


# ── Startup / shutdown ────────────────────────────────────────────────────────

# ── Leader election ────────────────────────────────────────────────────────────
# uvicorn runs multiple workers; without this EVERY worker would run the loops and
# dial the same outbound_call_queue rows -> duplicate calls. A Postgres
# session-level advisory lock elects exactly ONE worker to run the schedulers.
# The lock is held for the leader's lifetime on a dedicated connection and is
# auto-released if that process dies, so another worker can take over on restart.
_leader_conn = None
_LEADER_LOCK_KEY = 911287  # arbitrary app-wide constant


async def _acquire_leadership() -> bool:
    global _leader_conn
    import os
    # Genuine no-DB dev case: run anyway so a single process still schedules.
    if not os.environ.get("DATABASE_URL"):
        logger.warning("scheduler_no_database_url_running_anyway")
        return True
    try:
        import asyncpg
        _leader_conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        got = await _leader_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", _LEADER_LOCK_KEY
        )
        if not got:
            await _leader_conn.close()
            _leader_conn = None
        return bool(got)
    except Exception as exc:
        # DB IS configured but the lock attempt failed. Do NOT self-elect — two
        # workers both running schedulers would double-dial patients. Stand by.
        logger.warning("scheduler_leader_check_failed_standby", error=str(exc))
        if _leader_conn is not None:
            try:
                await _leader_conn.close()
            except Exception:
                pass
            _leader_conn = None
        return False


async def _bootstrap() -> None:
    """Elect a leader, then (only on the leader) spawn every background loop."""
    if not await _acquire_leadership():
        logger.info("scheduler_standby", reason="another worker holds leadership")
        return
    logger.info("scheduler_leader_elected")

    interval = getattr(settings, "REMINDER_INTERVAL_SECONDS", 900)
    asyncio.create_task(reminder_loop(interval), name="reminder_loop")
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

    if getattr(settings, "DOCTOR_AVAIL_ENABLED", True):
        da_interval = getattr(settings, "DOCTOR_AVAIL_INTERVAL_SECONDS", 600)
        asyncio.create_task(
            doctor_availability_loop(da_interval),
            name="doctor_availability_loop",
        )
        logger.info("doctor_avail_scheduler_started", interval_seconds=da_interval)

    if getattr(settings, "FOLLOWUPS_ENABLED", True):
        fu_interval = getattr(settings, "FOLLOWUP_LOOP_INTERVAL_SECONDS", 3600)
        fu_days = getattr(settings, "FOLLOWUP_DAYS_AFTER", 3)
        asyncio.create_task(
            followup_loop(fu_interval, fu_days),
            name="followup_loop",
        )
        logger.info("followup_scheduler_started",
                    interval_seconds=fu_interval, days_after=fu_days)

    if getattr(settings, "CAMPAIGN_RESUME_ENABLED", True):
        cr_interval = getattr(settings, "CAMPAIGN_RESUME_INTERVAL_SECONDS", 600)
        asyncio.create_task(
            campaign_resume_loop(cr_interval),
            name="campaign_resume_loop",
        )
        logger.info("campaign_resume_scheduler_started", interval_seconds=cr_interval)

    if getattr(settings, "OUTBOUND_QUEUE_ENABLED", True):
        oq_interval = getattr(settings, "OUTBOUND_QUEUE_INTERVAL_SECONDS", 300)
        asyncio.create_task(
            outbound_queue_loop(oq_interval),
            name="outbound_queue_loop",
        )
        logger.info("outbound_queue_scheduler_started", interval_seconds=oq_interval)

    if getattr(settings, "VOBIZ_CDR_ENABLED", False):
        cdr_interval = getattr(settings, "VOBIZ_CDR_INTERVAL_SECONDS", 600)
        asyncio.create_task(
            cdr_reconcile_loop(cdr_interval),
            name="cdr_reconcile_loop",
        )
        logger.info("cdr_reconcile_scheduler_started", interval_seconds=cdr_interval)


def start_scheduler() -> asyncio.Task | None:
    """Spawn the scheduler bootstrap (leader election + all loops). Returns the
    bootstrap task as the lifecycle handle for stop_scheduler()."""
    if not getattr(settings, "REMINDERS_ENABLED", True):
        logger.info("reminder_scheduler_disabled")
        return None
    _stop.clear()
    return asyncio.create_task(_bootstrap(), name="scheduler_bootstrap")


async def stop_scheduler(task: asyncio.Task | None) -> None:
    """Signal all loops to stop, release leadership, and await the bootstrap task."""
    global _leader_conn
    _stop.set()
    if _leader_conn is not None:
        try:
            await _leader_conn.execute("SELECT pg_advisory_unlock($1)", _LEADER_LOCK_KEY)
            await _leader_conn.close()
        except Exception:
            pass
        _leader_conn = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    logger.info("scheduler_stopped")
