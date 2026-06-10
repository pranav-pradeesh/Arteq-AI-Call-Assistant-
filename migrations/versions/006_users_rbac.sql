-- =============================================================================
-- Migration 006 — Users table & RBAC
-- =============================================================================
-- Idempotent: safe to run multiple times (uses IF NOT EXISTS / DO NOTHING).
-- Run order: after 005 (or after 004 if 005 does not exist yet).
--
-- Bootstrap note
-- --------------
-- The existing DASHBOARD_ADMIN_PASSWORD / DASHBOARD_JWT_SECRET single-password
-- auth (in dashboard/routes/auth.py) continues to work after this migration.
-- When you want to transition to per-user accounts, create a super_admin user
-- via POST /admin/users (or the INSERT below) and distribute their credentials.
-- The two auth mechanisms co-exist; DASHBOARD_ADMIN_PASSWORD can be retired
-- once all operators have per-user logins.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. users
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL
                              CHECK (role IN ('super_admin', 'tenant_admin', 'viewer')),
    active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for login lookup
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);


-- -----------------------------------------------------------------------------
-- 2. user_tenants  (many-to-many: user ↔ hospital slug)
-- -----------------------------------------------------------------------------
-- A super_admin does not need rows here — code grants them global access.
-- tenant_admin and viewer roles need at least one row to access any hospital.
-- tenant_slug references hospitals.slug (text, not a FK to avoid coupling;
--   add a FK constraint once you are confident slugs are stable):
--
--   CONSTRAINT fk_tenant_slug
--       FOREIGN KEY (tenant_slug) REFERENCES hospitals (slug) ON DELETE CASCADE
--
CREATE TABLE IF NOT EXISTS user_tenants (
    user_id     UUID  NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tenant_slug TEXT  NOT NULL,
    PRIMARY KEY (user_id, tenant_slug)
);

CREATE INDEX IF NOT EXISTS idx_user_tenants_user_id ON user_tenants (user_id);
CREATE INDEX IF NOT EXISTS idx_user_tenants_slug    ON user_tenants (tenant_slug);


-- -----------------------------------------------------------------------------
-- 3. Optional: seed a first super_admin account
-- -----------------------------------------------------------------------------
-- Replace the placeholder values before running in production.
-- Password below is the bcrypt hash of the string "ChangeMe!Secure123"
-- generated with:  python -c "from passlib.context import CryptContext; \
--   print(CryptContext(schemes=['bcrypt']).hash('ChangeMe!Secure123'))"
--
-- IMPORTANT: Change the email and regenerate the hash before deploying.
--
-- INSERT INTO users (email, password_hash, role)
-- VALUES (
--     'admin@yourhospital.com',
--     '$2b$12$ExampleHashReplaceThisWithARealBcryptHashGeneratedLocally',
--     'super_admin'
-- )
-- ON CONFLICT (email) DO NOTHING;
