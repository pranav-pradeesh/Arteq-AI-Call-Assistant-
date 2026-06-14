// Domain types mirroring the FastAPI backend (admin_api.py) + planned additions.
// 0 = Sunday ... 6 = Saturday (DB/Postgres EXTRACT(DOW) convention).

export type Tier = "hospital" | "clinic";
export type Role = "super_admin" | "tenant_admin" | "viewer";

export interface Hospital {
  id: string;
  name: string;
  name_ml?: string | null;
  address?: string | null;
  phone?: string | null;
  hours?: Record<string, [string, string]> | null;
  slug?: string | null;
  plivo_number?: string | null;
  knowledge_base?: string | null;
  tier?: Tier;
  agent_name?: string | null;
  active: boolean;
  created_at?: string;
}

export interface Department {
  id: string;
  hospital_id: string;
  name: string;
  name_ml?: string | null;
  floor?: string | null;
  location_hint?: string | null;
  phone_ext?: string | null;
  active: boolean;
}

export interface Schedule {
  id: string;
  doctor_id: string;
  hospital_id: string;
  day_of_week: number; // 0=Sun..6=Sat
  start_time: string; // "HH:MM"
  end_time: string;
  room?: string | null;
  active: boolean;
}

export interface Doctor {
  id: string;
  hospital_id: string;
  dept_id?: string | null;
  name: string;
  name_ml?: string | null;
  specialty?: string | null;
  qualifications?: string | null;
  active: boolean;
  schedules?: Schedule[];
}

export interface BillingItem {
  id: string;
  hospital_id: string;
  item: string;
  item_ml?: string | null;
  price_min?: number | null;
  price_max?: number | null;
  notes?: string | null;
  active: boolean;
}

export interface EmergencyContact {
  id: string;
  hospital_id: string;
  label: string;
  label_ml?: string | null;
  phone: string;
  priority: number;
  active: boolean;
}

export interface Faq {
  id: string;
  hospital_id: string;
  category?: string | null;
  question: string;
  answer: string;
  answer_ml?: string | null;
  tags?: string[];
  priority: number;
  active: boolean;
}

export type AppointmentStatus =
  | "pending"
  | "booked"
  | "confirmed"
  | "cancelled"
  | "rescheduled"
  | "requested";

export interface Appointment {
  id: string;
  hospital_id: string;
  patient_name?: string | null;
  patient_phone?: string | null;
  doctor_id?: string | null;
  dept_id?: string | null;
  slot_time?: string | null;
  notes?: string | null;
  call_id?: string | null;
  status: AppointmentStatus;
  reminder_sent: boolean;
  confirmation_sent: boolean;
  followup_sent: boolean;
  created_at?: string;
  updated_at?: string;
}

export type CallbackStatus = "pending" | "scheduled" | "completed" | "cancelled";

export interface Callback {
  id: string;
  hospital_id: string;
  patient_phone: string;
  patient_name?: string | null;
  reason?: string | null;
  preferred_time?: string | null;
  status: CallbackStatus;
  call_id?: string | null;
  created_at?: string;
}

export interface TranscriptTurn {
  role: "agent" | "user";
  text: string;
  ts?: string;
  latency_ms?: number;
}

export interface CallLog {
  id: string;
  hospital_id?: string | null;
  call_id?: string | null;
  caller?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  total_turns: number;
  latency_avg_ms: number;
  cost_paise: number;
  transcript?: TranscriptTurn[] | string | null;
  intents?: string[] | string | null;
  outcome?: string | null;
  created_at?: string;
}

export interface Stats {
  total_calls: number;
  avg_latency_ms: number;
  avg_turns: number;
  transfers: number;
}

// ── Planned additions (Subagent D backend) ──────────────────────────────
export interface AnalyticsPoint {
  bucket: string; // ISO date/hour
  calls: number;
  avg_latency_ms: number;
  cost_paise: number;
}

export interface AnalyticsSummary {
  total_calls: number;
  total_cost_paise: number;
  avg_latency_ms: number;
  avg_turns: number;
  outcomes: Record<string, number>;
  intents: Record<string, number>;
  languages: Record<string, number>;
  delta_calls_pct?: number;
}

export interface CallFeedback {
  id: string;
  call_id: string;
  hospital_id?: string | null;
  rating: number; // 1..5
  comments?: string | null;
  created_at?: string;
}

export interface MissedQuestion {
  id: string;
  hospital_id?: string | null;
  call_id?: string | null;
  question?: string | null;
  language?: string | null;
  context?: string | null;
  created_at?: string;
}

export interface User {
  id: string;
  email: string;
  role: Role;
  active: boolean;
  tenants?: string[]; // slugs the user is scoped to
  created_at?: string;
}

export interface Tenant {
  slug: string;
  name: string;
  name_ml?: string | null;
  db_url?: string | null;
  agent_name?: string | null;
  tier?: Tier;
  phone?: string | null;
  plivo_number?: string | null;
  address?: string | null;
  contact_person?: string | null;
  contact_phone?: string | null;
  notes?: string | null;
  features?: Record<string, boolean>;
}

export interface TelephonyStatus {
  overall: { sip_calls_ready: boolean };
  plivo?: Record<string, boolean>;
  livekit?: Record<string, boolean>;
  missing: string[];
  bsnl_forward_code?: string;
}

export interface SetupStatus {
  checks: Record<string, boolean>;
  bsnl_forward_code?: string;
}

export interface HisConfig {
  type: "fhir" | "generic_rest" | "none";
  base_url?: string;
  auth?: { type: string; header?: string; value?: string };
  endpoints?: Record<string, string>;
}
