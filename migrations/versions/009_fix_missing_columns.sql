-- Migration 009: Add columns that may be absent on databases created before 001_schema.sql
-- Idempotent — safe to re-run on any database state.

-- hospitals
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS active       BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS plivo_number TEXT;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS knowledge_base TEXT;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS tier         TEXT NOT NULL DEFAULT 'hospital';

-- departments
ALTER TABLE departments ADD COLUMN IF NOT EXISTS active      BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE departments ADD COLUMN IF NOT EXISTS phone_ext   TEXT;
ALTER TABLE departments ADD COLUMN IF NOT EXISTS location_hint TEXT;
ALTER TABLE departments ADD COLUMN IF NOT EXISTS name_ml     TEXT;
ALTER TABLE departments ADD COLUMN IF NOT EXISTS floor       TEXT;

-- doctors
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS active         BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS name_ml        TEXT;
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS qualifications TEXT;
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS bio            TEXT;

-- billing_info
ALTER TABLE billing_info ADD COLUMN IF NOT EXISTS active    BOOLEAN NOT NULL DEFAULT true;

-- faqs
ALTER TABLE faqs ADD COLUMN IF NOT EXISTS active            BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE faqs ADD COLUMN IF NOT EXISTS priority          INT NOT NULL DEFAULT 0;
ALTER TABLE faqs ADD COLUMN IF NOT EXISTS tags              TEXT[] DEFAULT '{}';
ALTER TABLE faqs ADD COLUMN IF NOT EXISTS answer_ml         TEXT;
ALTER TABLE faqs ADD COLUMN IF NOT EXISTS category          TEXT NOT NULL DEFAULT 'general';

-- announcements (may not exist on older schemas)
CREATE TABLE IF NOT EXISTS announcements (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    message     TEXT NOT NULL,
    message_ml  TEXT,
    active      BOOLEAN NOT NULL DEFAULT true,
    priority    INT NOT NULL DEFAULT 0,
    starts_at   TIMESTAMPTZ,
    ends_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS active   BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 0;
