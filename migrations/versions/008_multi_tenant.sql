-- Migration 008: Multi-tenant scale — 10+ hospitals, 15+ clinics
-- Idempotent — safe to re-run.

-- Per-hospital agent persona: each hospital can have its own AI name and language.
-- Defaults keep the system backward-compatible (Arya, Malayalam).
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS agent_name     TEXT DEFAULT 'Arya';
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS agent_language TEXT DEFAULT 'ml-IN';

-- Prevent double-booking at the DB level.
-- Two callers cannot book the same doctor into the same time slot simultaneously.
-- Cancelled / no-show appointments are excluded so the slot becomes reavailable.
CREATE UNIQUE INDEX IF NOT EXISTS ix_appt_no_double_book
    ON appointments (doctor_id, slot_time)
    WHERE status NOT IN ('cancelled', 'no_show');
