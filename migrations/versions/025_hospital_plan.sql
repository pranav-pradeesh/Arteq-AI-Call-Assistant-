-- Migration 025: per-hospital plan (trial | full).
-- trial = outbound calls + reminders + dashboard setup (doctors, patient import).
-- full  = everything, incl. inbound AI call answering.
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS plan text NOT NULL DEFAULT 'trial';
