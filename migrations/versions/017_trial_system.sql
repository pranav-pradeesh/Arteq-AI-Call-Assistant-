-- Migration 017: Trial / subscription system
-- Adds subscription_status + trial date columns to hospitals and tenants.
-- Existing rows are immediately set to 'active' (they predate the trial system).
-- Idempotent — safe to re-run.

ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS subscription_status TEXT        NOT NULL DEFAULT 'trial',
    ADD COLUMN IF NOT EXISTS trial_started_at    TIMESTAMPTZ          DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS trial_expires_at    TIMESTAMPTZ          DEFAULT (NOW() + INTERVAL '14 days'),
    ADD COLUMN IF NOT EXISTS activated_at        TIMESTAMPTZ;

-- subscription_status values: 'trial' | 'active' | 'expired' | 'cancelled'

-- Existing hospitals are live — grandfather them as active.
UPDATE hospitals SET subscription_status = 'active', activated_at = NOW()
WHERE subscription_status = 'trial';

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS subscription_status TEXT        NOT NULL DEFAULT 'trial',
    ADD COLUMN IF NOT EXISTS trial_started_at    TIMESTAMPTZ          DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS trial_expires_at    TIMESTAMPTZ          DEFAULT (NOW() + INTERVAL '14 days'),
    ADD COLUMN IF NOT EXISTS activated_at        TIMESTAMPTZ;

UPDATE tenants SET subscription_status = 'active', activated_at = NOW()
WHERE subscription_status = 'trial';

CREATE INDEX IF NOT EXISTS ix_hospitals_sub_status ON hospitals(subscription_status);
CREATE INDEX IF NOT EXISTS ix_tenants_sub_status   ON tenants(subscription_status);
