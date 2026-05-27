-- Arteq Hospital Voice Agent — Initial Schema
-- This runs once when the PostgreSQL container starts.
-- The full ORM-managed schema uses Alembic; this is for docker-compose initialization.

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for future fuzzy search if needed

-- Ensure uuid-ossp is available for UUID generation
SELECT uuid_generate_v4();
