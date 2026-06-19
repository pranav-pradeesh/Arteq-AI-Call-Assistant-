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
  plivo_number?: string | null; // carrier DID (Exotel); JSON key kept for backend compat
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

export type DoctorAvailability =
  | "available"
  | "busy"
  | "delayed"
  | "unavailable"
  | "on_leave";

export interface Doctor {
  id: string;
  hospital_id: string;
  dept_id?: string | null;
  name: string;
  name_ml?: string | null;
  specialty?: string | null;
  qualifications?: string | null;
  active: boolean;
  availability_status?: DoctorAvailability | null;
  schedules?: Schedule[];
}

export interface DoctorAvailabilityEvent {
  id: string;
  doctor_id: string;
  hospital_id?: string | null;
  status: DoctorAvailability;
  note?: string | null;
  created_at: string;
}

export interface DoctorAvailabilityInfo {
  availability_status: DoctorAvailability;
  events: DoctorAvailabilityEvent[];
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

// Workflow engine state (scheduler-driven confirmation/reminder/availability calls).
export type AppointmentWorkflowStatus =
  | "pending"
  | "confirmed"
  | "missed"
  | "cancelled"
  | "reminder_sent"
  | "doctor_available"
  | "doctor_delayed"
  | "doctor_unavailable";

export interface AppointmentEvent {
  id: string;
  appointment_id: string;
  hospital_id?: string | null;
  event_type: string; // e.g. "confirmation_call_placed", "confirmed", "missed", "reminder_sent"
  detail?: string | null;
  created_at: string;
}

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
  workflow_status?: AppointmentWorkflowStatus | null;
  doctor_availability_attempts?: number | null;
  doctor_availability_notified?: boolean | null;
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
  recording_url?: string | null; // audio recording of the call (when stored by backend)
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
  tenant_slugs?: string[]; // hospital slugs this user can access (tenant_admin / viewer)
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
  // Carrier blocks. The Telephony page renders whichever carrier block(s) the
  // backend sends (Vobiz is the current primary; Exotel/Plivo may also appear).
  vobiz?: Record<string, boolean>;
  exotel?: Record<string, boolean>;
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

// ── Patient-intake workflow (ported from the mock; backend endpoints planned,
//    see backend-patches/BACKEND_SPEC_patient_intake.md) ────────────────────
export interface Patient {
  id: string;
  hospital_id?: string | null;
  name: string;
  phone: string;
  created_at?: string;
}

export type PaymentMode = "pay_now" | "pay_later";

export type BookingStatus =
  | "pending_payment" // pay-now: QR generated, awaiting scan + pay
  | "awaiting_confirmation" // pay-later: token issued but inactive
  | "confirmed" // paid (pay-now) or token activated (pay-later)
  | "cancelled";

export interface BookingToken {
  code: string;
  active: boolean;
}

export interface Booking {
  id: string;
  hospital_id?: string | null;
  patient_id: string;
  patient_name: string;
  patient_phone: string;
  slot: string; // ISO datetime
  payment_mode: PaymentMode;
  status: BookingStatus;
  amount_paise: number;
  token?: BookingToken | null;
  created_at?: string;
}

export interface WhatsAppMessage {
  id: string;
  hospital_id?: string | null;
  phone: string;
  patient_name?: string | null;
  body: string;
  at: string; // ISO datetime
}

// ── Trial / subscription (migrations 017) ──────────────────────────────────
export type SubscriptionStatus = "trial" | "active" | "expired";

export interface TrialStatus {
  subscription_status: SubscriptionStatus;
  trial_started_at?: string | null;
  trial_expires_at?: string | null;
  activated_at?: string | null;
  days_remaining?: number | null;
}
