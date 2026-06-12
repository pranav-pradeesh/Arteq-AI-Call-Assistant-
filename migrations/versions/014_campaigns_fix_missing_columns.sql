-- Migration 014: Fix campaigns table columns missing on DBs created before 007_campaigns.sql
-- Idempotent — safe to re-run on any database state.
-- Root cause: CREATE TABLE IF NOT EXISTS in 007 is a no-op when campaigns table
-- pre-existed without the columns added in that migration.

-- Core columns from 007
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_template  TEXT NOT NULL DEFAULT '';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'draft';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS total_recipients  INT  NOT NULL DEFAULT 0;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS calls_placed      INT  NOT NULL DEFAULT 0;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS calls_answered    INT  NOT NULL DEFAULT 0;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS scheduled_at      TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS completed_at      TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- campaign_recipients columns (in case table pre-existed too)
ALTER TABLE campaign_recipients ADD COLUMN IF NOT EXISTS call_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE campaign_recipients ADD COLUMN IF NOT EXISTS called_at   TIMESTAMPTZ;
ALTER TABLE campaign_recipients ADD COLUMN IF NOT EXISTS call_sid    TEXT;

-- Indexes (idempotent via IF NOT EXISTS)
CREATE INDEX IF NOT EXISTS ix_campaign_hospital   ON campaigns(hospital_id);
CREATE INDEX IF NOT EXISTS ix_campaign_status     ON campaigns(status);
CREATE INDEX IF NOT EXISTS ix_camp_recip_campaign ON campaign_recipients(campaign_id);
CREATE INDEX IF NOT EXISTS ix_camp_recip_status   ON campaign_recipients(call_status);
