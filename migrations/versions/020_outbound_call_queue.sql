-- Migration 020: Outbound call queue — reliable retry tracking with calling-window
-- enforcement. Every scheduled outbound call is a queue row; the scheduler polls
-- this table and only dials within the allowed window (08:00–17:00 IST, max 3 tries).
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS outbound_call_queue (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id    UUID        REFERENCES hospitals(id),
    appointment_id UUID        REFERENCES appointments(id),
    -- call_type: 'confirmation' | 'reminder' | 'doctor_availability' | 'followup' | 'campaign'
    call_type      TEXT        NOT NULL,
    phone          TEXT        NOT NULL,
    patient_name   TEXT        NOT NULL DEFAULT '',
    context_json   JSONB       NOT NULL DEFAULT '{}',
    scheduled_at   TIMESTAMPTZ NOT NULL,
    attempt_count  INTEGER     NOT NULL DEFAULT 0,
    max_attempts   INTEGER     NOT NULL DEFAULT 3,
    attempted_at   TIMESTAMPTZ,
    -- status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled' | 'max_attempts'
    status         TEXT        NOT NULL DEFAULT 'pending',
    livekit_room   TEXT,
    call_log_id    TEXT,
    tenant_slug    TEXT        NOT NULL DEFAULT 'default',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ocq_status_sched ON outbound_call_queue(status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_ocq_hospital     ON outbound_call_queue(hospital_id);
CREATE INDEX IF NOT EXISTS ix_ocq_appointment  ON outbound_call_queue(appointment_id, call_type);
CREATE INDEX IF NOT EXISTS ix_ocq_tenant       ON outbound_call_queue(tenant_slug, status);
