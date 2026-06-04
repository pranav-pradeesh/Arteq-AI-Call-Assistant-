-- Migration 012: confirmation codes, offline-payment tokens, booking priority.
-- Idempotent — safe to re-run.
--
-- Booking flow now has two stages:
--   1) Appointment booked  → confirmation_code issued, payment_status='unpaid',
--      token NOT active. Patient is told the code over the phone.
--   2) Offline payment done → staff confirms payment → a queue token_number is
--      assigned (per doctor, per day, in priority order) and token_active=true.
--
-- priority orders the queue when two patients contend (emergency > senior >
-- earlier booking). Higher number = seen first.

-- Short human-readable booking reference spoken to the caller (e.g. "ARYA-7K3F").
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_code TEXT;

-- Offline payment lifecycle. 'waived' = no fee (e.g. follow-up / staff override).
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'unpaid';

-- Queue token, assigned only after payment is confirmed. NULL until then.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS token_number INT;

-- True once payment is confirmed and the token is live.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS token_active BOOLEAN NOT NULL DEFAULT false;

-- Queue priority. 0 = normal; bumped for seniors / emergencies. Higher = earlier.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 0;

-- Drop a stale CHECK if a prior run created one, then add the canonical constraint.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'appointments_payment_status_check'
    ) THEN
        ALTER TABLE appointments
            ADD CONSTRAINT appointments_payment_status_check
            CHECK (payment_status IN ('unpaid', 'paid', 'waived'));
    END IF;
END $$;

-- Confirmation code lookup (staff confirms payment by code), scoped to hospital.
CREATE INDEX IF NOT EXISTS ix_appt_confirmation_code
    ON appointments (hospital_id, confirmation_code)
    WHERE confirmation_code IS NOT NULL;

-- Load balancing: count today's active appointments per doctor cheaply.
-- (doctor_id, slot_time) leads so the planner scans only one doctor's day slice.
CREATE INDEX IF NOT EXISTS ix_appt_doctor_slot_active
    ON appointments (doctor_id, slot_time)
    WHERE status IN ('booked', 'confirmed', 'requested');
