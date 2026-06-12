-- Migration 013: call-quality persistence + outbound/HIS reliability tracking
-- Idempotent — safe to re-run.

-- Acoustic sensory summary per call (e.g. ["VOL=LOW","TENSION=TREMBLING"]) so
-- staff can review calls where the caller sounded distressed.
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS emotional_state TEXT;

-- HIS synchronisation outcome per appointment: NULL (no HIS configured),
-- 'synced', or 'failed' (booked/changed locally but the HIS write failed —
-- needs manual reconciliation).
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS his_sync_status TEXT;

-- Outbound dial attempt counters. The scheduler increments these on every
-- dial attempt; pending queries skip rows that have already been tried 3
-- times so a permanently unreachable number can't be redialed forever.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_attempts     INT NOT NULL DEFAULT 0;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS followup_attempts     INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_appt_his_sync
    ON appointments(his_sync_status) WHERE his_sync_status = 'failed';
