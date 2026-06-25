-- Migration 028: captured patient details on call_logs (for recording naming)
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS patient_name text;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS patient_age integer;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS patient_gender text;
