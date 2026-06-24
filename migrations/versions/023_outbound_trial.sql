-- Migration 023: Outbound reminder trial tier.
-- Adds per-hospital reminder toggles + appointment source tracking so the
-- CSV/Excel import flow can enqueue 24h/2h reminder calls per the hospital's
-- settings. Idempotent — safe to re-run.

-- ── Reminder toggles (per-hospital) ───────────────────────────────────────────
-- Default ON: the trial tier exists to place these calls. They only ever fire
-- for appointments enqueued by the import endpoint (source='import'), so
-- defaulting ON does not change behaviour for existing hospitals.
ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS remind_24h_enabled                  BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS remind_2h_enabled                   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS call_on_doctor_unavailable_enabled  BOOLEAN NOT NULL DEFAULT TRUE;

-- ── Appointment source ────────────────────────────────────────────────────────
-- source: 'manual' (dashboard CRUD) | 'import' (CSV/Excel) | 'inbound' (AI agent)
-- The time-based reminder_loop skips source='import' rows; those are driven
-- exclusively by the queue consumer so the 24h + 2h offsets fire independently.
ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual';

CREATE INDEX IF NOT EXISTS ix_appt_source ON appointments(source);

-- Dedupe support for the import endpoint: (hospital, doctor, phone, slot).
CREATE INDEX IF NOT EXISTS ix_appt_import_dedupe
    ON appointments(hospital_id, doctor_id, patient_phone, slot_time);

-- Prevent duplicate pending queue rows for the same appointment + call_type
-- (the import endpoint also checks in-app, this is the backstop).
CREATE UNIQUE INDEX IF NOT EXISTS ux_ocq_appt_calltype_pending
    ON outbound_call_queue(appointment_id, call_type)
    WHERE status = 'pending' AND appointment_id IS NOT NULL;
