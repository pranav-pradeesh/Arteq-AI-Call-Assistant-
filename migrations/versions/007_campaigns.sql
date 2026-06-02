-- Migration 007: Campaign tables for outbound health campaigns
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS campaigns (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id      UUID NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    campaign_type    TEXT NOT NULL,          -- health_camp | vaccination | checkup_reminder | custom
    message_template TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running', 'completed', 'cancelled')),
    total_recipients INT  NOT NULL DEFAULT 0,
    calls_placed     INT  NOT NULL DEFAULT 0,
    calls_answered   INT  NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_campaign_hospital ON campaigns(hospital_id);
CREATE INDEX IF NOT EXISTS ix_campaign_status   ON campaigns(status);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id  UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone        TEXT NOT NULL,
    call_status  TEXT NOT NULL DEFAULT 'pending'
                 CHECK (call_status IN ('pending', 'called', 'failed')),
    called_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_crecp_campaign ON campaign_recipients(campaign_id);
CREATE INDEX IF NOT EXISTS ix_crecp_status  ON campaign_recipients(call_status);
