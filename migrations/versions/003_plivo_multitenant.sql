-- Add multi-tenant slug and Plivo number columns to hospitals
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS slug TEXT;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS plivo_number TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS hospitals_slug_idx
    ON hospitals (slug)
    WHERE slug IS NOT NULL;
