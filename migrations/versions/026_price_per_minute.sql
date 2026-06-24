-- Migration 026: per-hospital price-per-minute (paise). NULL = not billed per minute.
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS price_per_minute_paise integer;
