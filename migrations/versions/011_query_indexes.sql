-- Migration 011: hot-path query indexes. Idempotent — safe to re-run.
--
-- Targets the exact WHERE/ORDER BY shapes the voice agent runs on every call,
-- so Postgres satisfies filter + sort + limit from a single index scan instead
-- of a seq scan + in-memory sort. No data change; pure read-speed.

-- 1) Returning-patient lookup (get_patient_profile, get_appointments_by_phone):
--    WHERE hospital_id=$ AND patient_phone=$ [AND status IN ...] ORDER BY created_at DESC LIMIT 3
--    Composite in filter→sort order lets the planner walk the index and stop at
--    LIMIT — no sort node. On the pre-greeting critical path.
CREATE INDEX IF NOT EXISTS ix_appt_phone_recent
    ON appointments (hospital_id, patient_phone, created_at DESC);

-- The old single-column phone index is now redundant: every real query also
-- filters hospital_id, which the composite above leads with. Fewer indexes =
-- faster INSERTs on the booking path.
DROP INDEX IF EXISTS ix_appt_phone;

-- 2) opd_queue_today view + get_all_opd_queue_estimates:
--    GROUP BY dept_id over today's booked rows. dept_id-leading index lets the
--    aggregate scan only the relevant department slices.
CREATE INDEX IF NOT EXISTS ix_appt_dept_slot
    ON appointments (dept_id, slot_time)
    WHERE dept_id IS NOT NULL;

-- 3) Scheduler sweeps (get_pending_followups / get_pending_confirmations):
--    most appointments already have *_sent = true, so partial indexes keep these
--    tiny and skip the bulk of the table on each hourly sweep.
CREATE INDEX IF NOT EXISTS ix_appt_followup_pending
    ON appointments (slot_time)
    WHERE followup_sent = false;

CREATE INDEX IF NOT EXISTS ix_appt_confirmation_pending
    ON appointments (slot_time)
    WHERE confirmation_sent = false;

-- 4) Slot availability (get_available_slots): schedule lookup by doctor + day.
CREATE INDEX IF NOT EXISTS ix_sched_doctor_dow
    ON schedules (doctor_id, day_of_week)
    WHERE active = true;
