-- Migration 005: HIS (Hospital Information System) integration config
-- Idempotent — safe to re-run.

-- Add his_config JSONB column to hospitals.
-- Example value stored here (auth stored in DB; treat as sensitive):
-- {
--   "enabled": true,
--   "type": "generic_rest",   -- "generic_rest" | "fhir"
--   "base_url": "https://api.their-his.com/v1",
--   "auth": {"type": "bearer", "value": "...token..."},
--   "endpoints": {
--     "search_patient": "GET /patients?phone={phone}",
--     "get_slots":      "GET /doctors/{doctor_id}/slots?date={date}",
--     "create_appointment": "POST /appointments",
--     "cancel_appointment": "POST /appointments/{appointment_id}/cancel"
--   },
--   "field_map": {
--     "his_patient_id": "id",
--     "his_doctor_id":  "consultant_id",
--     "appointment_date": "visit_date",
--     "appointment_time": "visit_time"
--   }
-- }
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='hospitals' AND column_name='his_config'
    ) THEN
        ALTER TABLE hospitals ADD COLUMN his_config JSONB;
    END IF;
END$$;

-- Track HIS appointment ID so we can cancel via the HIS later.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='appointments' AND column_name='his_appointment_id'
    ) THEN
        ALTER TABLE appointments ADD COLUMN his_appointment_id TEXT;
        COMMENT ON COLUMN appointments.his_appointment_id IS
            'Appointment ID from the external HIS, for sync and cancellation.';
    END IF;
END$$;
