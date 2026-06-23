-- Migration 023: let admins be created straight from the Supabase table editor.
--
-- Workflow in Supabase -> Table editor -> users -> Insert row:
--   email         = the login USERNAME (e.g. cityclinic)
--   plain_password = the password in plain text (auto-hashed, then cleared)
--   role          = 'tenant_admin'  (hospital admin) or 'super_admin'
--   hospital_id   = the hospital's UUID (from the hospitals table)  -- for tenant_admin
--   active        = true
-- The triggers below bcrypt the password and create the user_tenants scope link.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE users ADD COLUMN IF NOT EXISTS plain_password text;

-- Hash plain_password -> password_hash on insert/update, then clear it.
CREATE OR REPLACE FUNCTION users_hash_plain_password()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions, pg_temp
AS $fn$
BEGIN
    IF NEW.plain_password IS NOT NULL AND NEW.plain_password <> '' THEN
        NEW.password_hash := crypt(NEW.plain_password, gen_salt('bf', 12));
        NEW.plain_password := NULL;
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_users_hash_pw ON users;
CREATE TRIGGER trg_users_hash_pw
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_hash_plain_password();

-- When hospital_id is set, auto-create the tenant scope link.
CREATE OR REPLACE FUNCTION users_link_tenant()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $fn$
BEGIN
    IF NEW.hospital_id IS NOT NULL THEN
        INSERT INTO user_tenants (user_id, tenant_slug)
        SELECT NEW.id, h.slug FROM hospitals h WHERE h.id = NEW.hospital_id
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_users_link_tenant ON users;
CREATE TRIGGER trg_users_link_tenant
    AFTER INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_link_tenant();
