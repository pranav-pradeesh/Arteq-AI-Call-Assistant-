-- Migration 002: extend appointments + add callbacks, call_feedback, opd_queue view
-- Run once in Supabase SQL Editor. All statements are idempotent (IF NOT EXISTS / IF EXISTS).

-- ── Extend existing appointments table ───────────────────────────────────────
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS dept_id   UUID REFERENCES departments(id);
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS call_id   TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS notes     TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Ensure status constraint covers all used values
DO $$
BEGIN
  ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_status_check;
  ALTER TABLE appointments ADD CONSTRAINT appointments_status_check
    CHECK (status IN ('pending','booked','confirmed','cancelled','rescheduled'));
EXCEPTION WHEN others THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_appt_phone    ON appointments(patient_phone);
CREATE INDEX IF NOT EXISTS ix_appt_slot     ON appointments(slot_time);
CREATE INDEX IF NOT EXISTS ix_appt_status   ON appointments(status);
CREATE INDEX IF NOT EXISTS ix_appt_dept     ON appointments(dept_id);
CREATE INDEX IF NOT EXISTS ix_appt_reminder ON appointments(reminder_sent, slot_time)
    WHERE reminder_sent = FALSE AND status IN ('booked','confirmed');

-- ── callbacks ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS callbacks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id     UUID NOT NULL REFERENCES hospitals(id),
    patient_phone   TEXT NOT NULL,
    patient_name    TEXT,
    reason          TEXT,
    preferred_time  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','scheduled','completed','cancelled')),
    call_id         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_cb_hospital ON callbacks(hospital_id);
CREATE INDEX IF NOT EXISTS ix_cb_status   ON callbacks(status);
CREATE INDEX IF NOT EXISTS ix_cb_phone    ON callbacks(patient_phone);

-- ── call_feedback ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id     TEXT NOT NULL,
    hospital_id UUID REFERENCES hospitals(id),
    rating      SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comments    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_feedback_call ON call_feedback(call_id);

-- ── opd_queue_today view (live queue count per dept) ─────────────────────────
CREATE OR REPLACE VIEW opd_queue_today AS
SELECT
    dept_id,
    COUNT(*)            AS queue_count,
    MIN(slot_time)      AS first_slot,
    MAX(slot_time)      AS last_slot
FROM appointments
WHERE
    status IN ('booked', 'confirmed')
    AND slot_time::date = CURRENT_DATE
    AND dept_id IS NOT NULL
GROUP BY dept_id;
