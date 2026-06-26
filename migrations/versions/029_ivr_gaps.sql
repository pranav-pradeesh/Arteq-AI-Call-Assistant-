-- Migration 029: IVR-parity gaps
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS greeting text;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS staff_alert_phone text;
ALTER TABLE departments ADD COLUMN IF NOT EXISTS timings text;
CREATE TABLE IF NOT EXISTS hospital_holidays (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_id uuid NOT NULL,
  holiday_date date NOT NULL,
  reason text,
  closed boolean NOT NULL DEFAULT true,
  open_time text,
  close_time text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hospital_id, holiday_date)
);
