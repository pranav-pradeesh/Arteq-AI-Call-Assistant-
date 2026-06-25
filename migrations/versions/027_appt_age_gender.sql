-- Migration 027: structured patient age + gender on appointments
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_age integer;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_gender text;
