-- Migration 018: Doctor availability tracking
-- Adds availability_status to doctors + append-only audit log.
-- Idempotent — safe to re-run.

-- availability_status: 'available' | 'busy' | 'delayed' | 'unavailable' | 'on_leave'
ALTER TABLE doctors
    ADD COLUMN IF NOT EXISTS availability_status TEXT NOT NULL DEFAULT 'available';

CREATE TABLE IF NOT EXISTS doctor_availability_events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id   UUID        REFERENCES doctors(id),
    hospital_id UUID        REFERENCES hospitals(id),
    status      TEXT        NOT NULL,
    note        TEXT,
    changed_by  TEXT,                              -- staff email or 'system'
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_dae_doctor   ON doctor_availability_events(doctor_id,   changed_at DESC);
CREATE INDEX IF NOT EXISTS ix_dae_hospital ON doctor_availability_events(hospital_id, changed_at DESC);
