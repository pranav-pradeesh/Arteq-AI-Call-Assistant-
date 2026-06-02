-- Migration 001: Complete schema for Arteq Hospital Voice Agent
-- Idempotent — safe to run multiple times (IF NOT EXISTS everywhere).
-- Run against a fresh Supabase / PostgreSQL database before first deploy.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── hospitals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hospitals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    name_ml     TEXT,
    address     TEXT,
    phone       TEXT,
    hours       JSONB,               -- {"mon":["08:00","20:00"], ...}
    slug        TEXT UNIQUE,         -- URL-safe identifier; used as room-name prefix
    plivo_number TEXT,               -- E.164 DID provisioned for this hospital
    knowledge_base TEXT,             -- free-form staff handbook
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── departments ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS departments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id  UUID NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    name_ml      TEXT,
    floor        TEXT,
    location_hint TEXT,
    phone_ext    TEXT,
    active       BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_dept_hospital ON departments(hospital_id);

-- ── doctors ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctors (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id  UUID NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    dept_id      UUID REFERENCES departments(id),
    name         TEXT NOT NULL,
    name_ml      TEXT,
    specialty    TEXT,
    qualifications TEXT,
    active       BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_doc_hospital ON doctors(hospital_id);

-- ── schedules (doctor consulting hours) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS schedules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id   UUID NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    hospital_id UUID NOT NULL REFERENCES hospitals(id),
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),  -- 0=Sun
    start_time  TIME NOT NULL,
    end_time    TIME NOT NULL,
    room        TEXT,
    active      BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_sched_doctor ON schedules(doctor_id);

-- ── billing_info ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS billing_info (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID NOT NULL REFERENCES hospitals(id),
    item        TEXT NOT NULL,       -- e.g. "consultation:general"
    item_ml     TEXT,
    price_min   NUMERIC(10,2),
    price_max   NUMERIC(10,2),
    notes       TEXT,
    active      BOOLEAN NOT NULL DEFAULT true
);

-- ── faqs ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS faqs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID NOT NULL REFERENCES hospitals(id),
    category    TEXT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    answer_ml   TEXT,
    tags        JSONB DEFAULT '[]',
    priority    INTEGER DEFAULT 0,
    active      BOOLEAN NOT NULL DEFAULT true
);

-- ── emergency_contacts ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS emergency_contacts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID NOT NULL REFERENCES hospitals(id),
    label       TEXT NOT NULL,
    label_ml    TEXT,
    phone       TEXT NOT NULL,
    priority    INTEGER DEFAULT 0,
    active      BOOLEAN NOT NULL DEFAULT true
);

-- ── appointments ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS appointments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id     UUID NOT NULL REFERENCES hospitals(id),
    patient_name    TEXT,
    patient_phone   TEXT,
    doctor_id       UUID REFERENCES doctors(id),
    dept_id         UUID REFERENCES departments(id),
    slot_time       TIMESTAMPTZ,
    notes           TEXT,
    call_id         TEXT,
    status          TEXT NOT NULL DEFAULT 'booked'
                    CHECK (status IN ('pending','booked','confirmed','cancelled','rescheduled','requested')),
    reminder_sent   BOOLEAN NOT NULL DEFAULT false,
    confirmation_sent BOOLEAN NOT NULL DEFAULT false,
    followup_sent   BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_appt_hospital  ON appointments(hospital_id);
CREATE INDEX IF NOT EXISTS ix_appt_phone     ON appointments(patient_phone);
CREATE INDEX IF NOT EXISTS ix_appt_slot      ON appointments(slot_time);
CREATE INDEX IF NOT EXISTS ix_appt_status    ON appointments(status);
CREATE INDEX IF NOT EXISTS ix_appt_reminder  ON appointments(reminder_sent, slot_time)
    WHERE reminder_sent = FALSE AND status IN ('booked','confirmed');

-- ── callbacks ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS callbacks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id     UUID NOT NULL REFERENCES hospitals(id),
    patient_phone   TEXT NOT NULL,
    patient_name    TEXT,
    reason          TEXT,
    preferred_time  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','scheduled','completed','cancelled')),
    call_id         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_cb_hospital ON callbacks(hospital_id);
CREATE INDEX IF NOT EXISTS ix_cb_status   ON callbacks(status);

-- ── call_logs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id     UUID REFERENCES hospitals(id),
    call_id         TEXT UNIQUE,
    caller          TEXT,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    total_turns     INTEGER DEFAULT 0,
    latency_avg_ms  INTEGER DEFAULT 0,
    cost_paise      INTEGER DEFAULT 0,
    transcript      TEXT,     -- JSON array stored as text
    intents         TEXT,     -- JSON array stored as text
    outcome         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_log_hospital ON call_logs(hospital_id);
CREATE INDEX IF NOT EXISTS ix_log_started  ON call_logs(started_at);

-- ── call_feedback ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id     TEXT NOT NULL,
    hospital_id UUID REFERENCES hospitals(id),
    rating      SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comments    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── missed_questions ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS missed_questions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID REFERENCES hospitals(id),
    call_id     TEXT,
    question    TEXT,
    language    TEXT,
    context     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── opd_queue_today view ──────────────────────────────────────────────────────
CREATE OR REPLACE VIEW opd_queue_today AS
SELECT
    dept_id,
    COUNT(*)       AS queue_count,
    MIN(slot_time) AS first_slot,
    MAX(slot_time) AS last_slot
FROM appointments
WHERE
    status IN ('booked', 'confirmed')
    AND slot_time::date = CURRENT_DATE
    AND dept_id IS NOT NULL
GROUP BY dept_id;

-- ── Seed: default hospital (matches HOSPITAL_ID in .env.example) ──────────────
INSERT INTO hospitals (id, name, name_ml, address, phone, hours, slug, active)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Arteq Demo Hospital',
    'ആർടെക് ഡെമോ ആശുപത്രി',
    'Demo Street, Thrissur, Kerala 680001',
    '+914872442888',
    '{"mon":["08:00","20:00"],"tue":["08:00","20:00"],"wed":["08:00","20:00"],
      "thu":["08:00","20:00"],"fri":["08:00","20:00"],"sat":["08:00","17:00"],
      "sun":["09:00","14:00"]}',
    'demo',
    true
)
ON CONFLICT (id) DO NOTHING;

-- Seed demo departments
INSERT INTO departments (hospital_id, name, name_ml, floor, location_hint, phone_ext, active)
SELECT '00000000-0000-0000-0000-000000000001', name, name_ml, floor, hint, ext, true
FROM (VALUES
    ('OPD / General Medicine', 'OPD / പൊതുവൈദ്യശാസ്ത്രം', 'Ground Floor', 'Main entrance, turn left', '101'),
    ('Emergency / Casualty',  'അടിയന്തിരം',              'Ground Floor', 'Separate entrance on north side', '999'),
    ('Cardiology',            'ഹൃദ്രോഗശാസ്ത്രം',          '2nd Floor',    'Block B', '201'),
    ('Orthopaedics',          'അസ്ഥിചികിത്സ',             '1st Floor',    'Block A', '151'),
    ('Paediatrics',           'ശിശുചികിത്സ',              'Ground Floor', 'Block C', '102'),
    ('Laboratory',            'ലാബ്',                      'Ground Floor', 'Behind pharmacy', '120'),
    ('Pharmacy',              'ഫാർമസി',                   'Ground Floor', 'Main entrance, right side', '110')
) AS t(name, name_ml, floor, hint, ext)
WHERE NOT EXISTS (
    SELECT 1 FROM departments WHERE hospital_id = '00000000-0000-0000-0000-000000000001' AND name = t.name
);

-- Seed demo FAQs
INSERT INTO faqs (hospital_id, category, question, answer, answer_ml, tags, priority, active)
SELECT '00000000-0000-0000-0000-000000000001', category, question, answer, answer_ml, tags::jsonb, priority, true
FROM (VALUES
    ('general', 'What are the visiting hours?',
     'Visiting hours are 9 AM to 11 AM and 4 PM to 6 PM daily.',
     'സന്ദർശന സമയം രാവിലെ 9 മുതൽ 11 വരെ, വൈകിട്ട് 4 മുതൽ 6 വരെ.',
     '["visiting","hours","timing"]', 1),
    ('general', 'Is there parking available?',
     'Free parking is available in the basement and adjacent lot.',
     'ബേസ്‌മെന്റിൽ സൗജന്യ പാർക്കിംഗ് ലഭ്യമാണ്.',
     '["parking","car"]', 1),
    ('emergency', 'Is the emergency open 24/7?',
     'Yes, our Emergency and Casualty department is open 24 hours, 7 days a week.',
     'അതെ, Emergency 24 മണിക്കൂറും തുറന്നിരിക്കും.',
     '["emergency","24x7","casualty"]', 2)
) AS t(category, question, answer, answer_ml, tags, priority)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs WHERE hospital_id = '00000000-0000-0000-0000-000000000001' AND question = t.question
);
