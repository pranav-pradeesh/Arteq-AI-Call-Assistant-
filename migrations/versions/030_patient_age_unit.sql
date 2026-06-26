-- Migration 030: age unit (years|months|weeks|days) — patients can be any age
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_age_unit text DEFAULT 'years';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS patient_age_unit text DEFAULT 'years';
