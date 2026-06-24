-- Migration 006b: bootstrap super-admin seed.
-- DISABLED for multi-tenant deployments. The super admin is provisioned by the
-- SUPERADMIN_EMAIL auto-upsert in src/main.py using DASHBOARD_ADMIN_PASSWORD.
-- A no-op statement keeps the idempotent migration runner happy (it cannot
-- execute a comment-only file).
SELECT 1;
