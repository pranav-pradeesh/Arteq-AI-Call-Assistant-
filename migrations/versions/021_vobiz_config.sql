-- Migration 021: Vobiz SIP trunk configuration columns on hospitals + tenants.
-- Idempotent — safe to re-run.

ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS vobiz_phone_number      TEXT,
    ADD COLUMN IF NOT EXISTS vobiz_inbound_trunk_id  TEXT,
    ADD COLUMN IF NOT EXISTS vobiz_outbound_trunk_id TEXT;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS vobiz_phone_number      TEXT,
    ADD COLUMN IF NOT EXISTS vobiz_inbound_trunk_id  TEXT,
    ADD COLUMN IF NOT EXISTS vobiz_outbound_trunk_id TEXT;
