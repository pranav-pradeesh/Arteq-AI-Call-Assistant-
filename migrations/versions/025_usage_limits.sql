-- 025_usage_limits.sql
-- Per-hospital plan + monthly usage limits, so each hospital's call usage can be
-- tracked against the plan we sell and (later) capped.
--
-- Usage itself is NOT stored here — it is computed live from call_logs
-- (cost_paise + started_at/ended_at). This migration only records the PLAN:
-- the allowances and the billing-cycle anchor. Idempotent — safe to re-run.

ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS plan_name                TEXT,     -- e.g. 'trial','starter','growth'
    ADD COLUMN IF NOT EXISTS monthly_call_limit       INTEGER,  -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS monthly_minutes_limit    INTEGER,  -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS monthly_cost_limit_paise BIGINT,   -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS billing_cycle_day        SMALLINT NOT NULL DEFAULT 1;

-- Keep the cycle anchor in the 1..28 range so monthly arithmetic never overflows
-- a short month (no 29/30/31 edge cases).
ALTER TABLE hospitals DROP CONSTRAINT IF EXISTS hospitals_billing_cycle_day_check;
ALTER TABLE hospitals
    ADD CONSTRAINT hospitals_billing_cycle_day_check
    CHECK (billing_cycle_day BETWEEN 1 AND 28);

-- Same plan fields on the tenant registry (control plane) for parity, so a
-- tenant's plan can be set at onboarding before any hospital row is scoped.
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS plan_name                TEXT,
    ADD COLUMN IF NOT EXISTS monthly_call_limit       INTEGER,
    ADD COLUMN IF NOT EXISTS monthly_minutes_limit    INTEGER,
    ADD COLUMN IF NOT EXISTS monthly_cost_limit_paise BIGINT,
    ADD COLUMN IF NOT EXISTS billing_cycle_day        SMALLINT NOT NULL DEFAULT 1;

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_billing_cycle_day_check;
ALTER TABLE tenants
    ADD CONSTRAINT tenants_billing_cycle_day_check
    CHECK (billing_cycle_day BETWEEN 1 AND 28);

-- ── Per-service cost breakdown on each call ───────────────────────────────────
-- The agent already prices STT / TTS / LLM separately; persist each component so
-- the dashboards can show live spend per service (Sarvam STT, Sarvam TTS, the
-- LLM, and Vobiz telephony) rather than one lumped figure. cost_paise stays the
-- total ( = stt + tts + llm + telephony ).
ALTER TABLE call_logs
    ADD COLUMN IF NOT EXISTS stt_paise       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tts_paise       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_paise       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS telephony_paise INTEGER NOT NULL DEFAULT 0;

-- Real measured usage behind those figures (cost = real usage × published rate,
-- not a flat per-turn guess). LLM tokens are only knowable at call time, so they
-- must be persisted; STT seconds / TTS chars are derivable but stored for audit.
ALTER TABLE call_logs
    ADD COLUMN IF NOT EXISTS llm_prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_completion_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stt_audio_seconds     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tts_chars             INTEGER NOT NULL DEFAULT 0;

-- Telephony is reconciled against the real Vobiz CDR cost asynchronously.
-- direction distinguishes inbound/outbound (needed to match a CDR record and to
-- report usage); cdr_reconciled flips true once the real INR cost is written.
ALTER TABLE call_logs
    ADD COLUMN IF NOT EXISTS direction      TEXT,
    ADD COLUMN IF NOT EXISTS cdr_reconciled BOOLEAN NOT NULL DEFAULT FALSE;

-- The reconciliation job scans for un-reconciled telephony rows by time.
CREATE INDEX IF NOT EXISTS ix_log_cdr_pending
    ON call_logs (started_at)
    WHERE cdr_reconciled = FALSE;

-- Usage is read by hospital + time window; the existing ix_log_hospital +
-- ix_log_started indexes already cover that access pattern.
