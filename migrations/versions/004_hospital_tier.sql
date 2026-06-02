-- Migration 004: Hospital tier support (clinic vs hospital)
-- Idempotent — safe to re-run.

-- Add tier column to hospitals.
-- clinic  = small practice, 1-10 doctors, no outbound scheduler by default
-- hospital = full feature set
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='hospitals' AND column_name='tier'
    ) THEN
        ALTER TABLE hospitals ADD COLUMN tier TEXT NOT NULL DEFAULT 'hospital'
            CHECK (tier IN ('clinic', 'hospital'));
    END IF;
END$$;

-- Clinics default to disabled outbound features; hospitals keep them on.
-- (Feature flags live in env vars but tier is the DB-level hint for display.)
