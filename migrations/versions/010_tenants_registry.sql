-- Migration 010: Tenant registry (control plane)
-- Idempotent — safe to re-run.
--
-- The registry lives in the CONTROL database (settings.DATABASE_URL). It maps a
-- hospital/clinic slug to the connection string of that tenant's OWN Supabase
-- database, plus its tier and per-tenant feature flags. Operational data
-- (departments, doctors, appointments, call_logs, …) lives in each tenant's
-- own DB, addressed via tenants.db_url.
--
-- features is a JSON object of {feature_key: bool}. Defaults are applied from
-- the tier matrix at onboarding (see src/tenancy/features.py) and are editable
-- per tenant from the admin dashboard.

CREATE TABLE IF NOT EXISTS tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    name_ml         TEXT DEFAULT '',
    tier            TEXT NOT NULL DEFAULT 'hospital',   -- 'hospital' | 'clinic'
    db_url          TEXT DEFAULT '',                    -- tenant's own Supabase connection string
    features        JSONB NOT NULL DEFAULT '{}'::jsonb, -- {feature_key: bool}
    plivo_number    TEXT DEFAULT '',
    agent_name      TEXT DEFAULT 'Arya',
    agent_language  TEXT DEFAULT 'ml-IN',
    address         TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    contact_person  TEXT DEFAULT '',
    contact_phone   TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tenants_slug   ON tenants (slug);
CREATE INDEX IF NOT EXISTS ix_tenants_active ON tenants (active);
