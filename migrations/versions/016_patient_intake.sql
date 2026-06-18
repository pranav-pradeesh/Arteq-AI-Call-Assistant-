-- Migration 016: patient intake workflow tables
-- Adds: patients, bookings, whatsapp_messages
-- Idempotent — safe to re-run.

-- ── patients ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patients (
    id          TEXT PRIMARY KEY,                  -- "P-YYMMDD-NNN"
    hospital_id UUID REFERENCES hospitals(id),
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_patients_hospital ON patients(hospital_id);

-- ── bookings ──────────────────────────────────────────────────────────────────
-- Bookings are front-desk appointments separate from the AI-booked `appointments`
-- table. They carry their own token and payment workflow.
CREATE TABLE IF NOT EXISTS bookings (
    id              TEXT PRIMARY KEY,              -- "appt-{hex6}"
    hospital_id     UUID REFERENCES hospitals(id),
    patient_id      TEXT REFERENCES patients(id),
    slot            TIMESTAMPTZ,
    payment_mode    TEXT NOT NULL DEFAULT 'pay_now',  -- "pay_now"|"pay_later"
    status          TEXT NOT NULL DEFAULT 'pending_payment',
    amount_paise    INTEGER NOT NULL DEFAULT 0,
    token_code      TEXT,                          -- "TKN-4821" or NULL
    token_active    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_bookings_hospital   ON bookings(hospital_id);
CREATE INDEX IF NOT EXISTS ix_bookings_patient    ON bookings(patient_id);
CREATE INDEX IF NOT EXISTS ix_bookings_status     ON bookings(status);

-- ── whatsapp_messages ─────────────────────────────────────────────────────────
-- Append-only log of every outbound WhatsApp message the system sends.
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id              TEXT PRIMARY KEY,              -- "wa-{uuid8}"
    hospital_id     UUID REFERENCES hospitals(id),
    phone           TEXT NOT NULL,
    patient_name    TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL,
    at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wa_hospital ON whatsapp_messages(hospital_id);
CREATE INDEX IF NOT EXISTS ix_wa_at       ON whatsapp_messages(hospital_id, at DESC);
