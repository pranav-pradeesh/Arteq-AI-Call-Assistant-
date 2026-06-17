-- Migration 015: add recording_url to call_logs
-- Idempotent — safe to re-run.

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS recording_url TEXT;
