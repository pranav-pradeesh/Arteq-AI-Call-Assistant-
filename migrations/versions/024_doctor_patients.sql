-- Migration 024: per-doctor patient roster (imported from CSV/XLSX).
-- Tracks the patients a doctor has previously seen. Separate from the live
-- intake `patients` table so imports don't pollute call-flow data.
CREATE TABLE IF NOT EXISTS doctor_patients (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id uuid NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    doctor_id   uuid NOT NULL REFERENCES doctors(id)   ON DELETE CASCADE,
    name        text NOT NULL DEFAULT '',
    phone       text NOT NULL DEFAULT '',
    last_visit  text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doctor_patients_doctor   ON doctor_patients(doctor_id);
CREATE INDEX IF NOT EXISTS idx_doctor_patients_hospital ON doctor_patients(hospital_id);
