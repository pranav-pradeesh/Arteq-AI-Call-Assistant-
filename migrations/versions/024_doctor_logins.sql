-- 024_doctor_logins.sql
-- Doctor self-service logins: allow role='doctor' and link a user to a doctors
-- row so a doctor can log in and see ONLY their own data.

-- 1. Allow the new role. The CHECK from 006a_users_rbac.sql is unnamed, so
--    Postgres auto-named it users_role_check.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users
    ADD CONSTRAINT users_role_check
    CHECK (role IN ('super_admin', 'tenant_admin', 'viewer', 'doctor'));

-- 2. Link a doctor login to its doctors row + hospital (NULL for other users).
ALTER TABLE users ADD COLUMN IF NOT EXISTS doctor_id   UUID REFERENCES doctors(id)   ON DELETE CASCADE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS hospital_id UUID REFERENCES hospitals(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_users_doctor ON users (doctor_id);

-- 3. Integrity: a doctor login MUST point at a doctors row, and only a doctor
--    login may. Existing non-doctor rows have doctor_id NULL → already valid.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_doctor_link_check;
ALTER TABLE users
    ADD CONSTRAINT users_doctor_link_check
    CHECK (
        (role = 'doctor' AND doctor_id IS NOT NULL)
        OR (role <> 'doctor' AND doctor_id IS NULL)
    );
