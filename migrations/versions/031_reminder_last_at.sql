-- Migration 031: track when the last reminder call fired (for 3x/day windows)
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS last_reminder_at timestamptz;
