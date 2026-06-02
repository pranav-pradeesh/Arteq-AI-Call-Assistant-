-- Migration 006: reconcile status enums + callbacks.attempted_at with the
-- admin dashboard and the agent tools. Idempotent — safe to run repeatedly.
--
-- Why: the dashboard offers appointment statuses 'completed'/'no_show' and
-- callback statuses 'attempted'/'failed', and shows a callbacks "attempted at"
-- column. The original CHECK constraints rejected those values (runtime
-- CheckViolation) and the column did not exist. This widens both constraints
-- and adds the missing column so the UI works in dev and production alike.

-- ── appointments.status ───────────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_status_check;
  ALTER TABLE appointments ADD CONSTRAINT appointments_status_check
    CHECK (status IN (
      'pending','booked','confirmed','cancelled','rescheduled',
      'requested','completed','no_show'
    ));
EXCEPTION WHEN others THEN NULL;
END $$;

-- ── callbacks.status + attempted_at ───────────────────────────────────────────
ALTER TABLE callbacks ADD COLUMN IF NOT EXISTS attempted_at TIMESTAMPTZ;

DO $$
BEGIN
  ALTER TABLE callbacks DROP CONSTRAINT IF EXISTS callbacks_status_check;
  ALTER TABLE callbacks ADD CONSTRAINT callbacks_status_check
    CHECK (status IN (
      'pending','scheduled','completed','cancelled','attempted','failed'
    ));
EXCEPTION WHEN others THEN NULL;
END $$;
