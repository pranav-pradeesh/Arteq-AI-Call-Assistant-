-- Migration 019: Appointment workflow — extended status tracking + audit log.
-- Idempotent — safe to re-run.

-- ── appointments extensions ───────────────────────────────────────────────────
-- workflow_status: 'pending' | 'confirmed' | 'cancelled' | 'missed'
--                 | 'reminder_sent' | 'doctor_available' | 'doctor_delayed'
--                 | 'doctor_unavailable'
ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS workflow_status              TEXT        NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS doctor_availability_attempts INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS doctor_availability_notified BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS workflow_updated_at          TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_appts_workflow_status ON appointments(workflow_status);

-- ── appointment_events — immutable audit trail ─────────────────────────────────
-- event_type values:
--   'status_change' | 'call_attempted' | 'call_answered' | 'call_missed'
--   | 'sms_sent' | 'whatsapp_sent' | 'doctor_status_update'
CREATE TABLE IF NOT EXISTS appointment_events (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_id  UUID        NOT NULL REFERENCES appointments(id),
    hospital_id     UUID        REFERENCES hospitals(id),
    event_type      TEXT        NOT NULL,
    old_status      TEXT,
    new_status      TEXT,
    note            TEXT,
    actor           TEXT        NOT NULL DEFAULT 'system',
    -- 'system' | 'staff:<email>' | 'patient'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_appt_events_appt ON appointment_events(appointment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_appt_events_hosp ON appointment_events(hospital_id,    created_at DESC);
