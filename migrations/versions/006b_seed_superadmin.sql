-- Migration 006b: seed the bootstrap super-admin user.
-- Idempotent. Run AFTER 006_users_rbac.sql.
--
-- The password_hash below is a bcrypt ($2b$, cost 12) hash of the CURRENT
-- DASHBOARD_ADMIN_PASSWORD. passlib's CryptContext(schemes=["bcrypt"]) in
-- users_api.py verifies it directly.
--
-- SECURITY: rotate this immediately after first login. To regenerate a hash
-- for a new password, run:
--     python -c "from passlib.hash import bcrypt; print(bcrypt.using(rounds=12).hash('NEW_PASSWORD'))"
-- and replace the value below (or UPDATE the row).

INSERT INTO users (email, password_hash, role, active)
VALUES (
    'mohammedhayyan@arteqai.com',
    '$2b$12$18YD/0iwZaDOFuTTq776VOF62JPh.dbShZy1fOM75rKciG5ckeuI.',
    'super_admin',
    true
)
ON CONFLICT (email) DO NOTHING;
