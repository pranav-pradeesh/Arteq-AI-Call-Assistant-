-- Migration 032: per-hospital human/reception transfer number
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS reception_phone text;
